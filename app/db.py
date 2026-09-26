import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet, InvalidToken
from app.models import Settings, ScanSnapshot
from app.crawler.parser import merge_orders


class Repository:
    def __init__(self, path: Path, encryption_key: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(encryption_key.encode())
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scopes(namespace TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders(
                    namespace TEXT, shop TEXT, order_id TEXT, payload TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    PRIMARY KEY(namespace,shop,order_id));
                CREATE TABLE IF NOT EXISTS outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, chat_id TEXT NOT NULL, topic_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry REAL NOT NULL DEFAULT 0, message_id INTEGER,
                    error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS scans(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL NOT NULL,
                    finished_at REAL, status TEXT NOT NULL, pages INTEGER DEFAULT 0,
                    orders_count INTEGER DEFAULT 0, new_orders INTEGER DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS sessions(
                    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL, generation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cooldown(id INTEGER PRIMARY KEY, until REAL NOT NULL);
            """)
        self.settings()  # Validate encryption before background work starts.

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self) -> Settings:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
        if not row:
            return Settings()
        raw = json.loads(row["payload"])
        try:
            for field in ("password", "bot_token"):
                raw[field] = self.cipher.decrypt(raw[field].encode()).decode() if raw.get(field) else ""
        except InvalidToken:
            raise RuntimeError("Không giải mã được cấu hình. Khôi phục SETTINGS_ENCRYPTION_KEY đúng.") from None
        return Settings(**raw)

    def public_settings(self):
        raw = self.settings().model_dump()
        for field in ("password", "bot_token"):
            raw[field + "_configured"] = bool(raw.pop(field))
        return raw

    def merged_settings(self, changes: dict) -> Settings:
        raw = self.settings().model_dump()
        changes = dict(changes)
        for field in ("password", "bot_token"):
            if changes.pop("clear_" + field, False):
                raw[field] = ""
            if changes.get(field) == "":
                changes.pop(field)
        raw.update(changes)
        return Settings(**raw)

    def update_settings(self, changes: dict) -> Settings:
        settings = self.merged_settings(changes)
        raw = settings.model_dump()
        for field in ("password", "bot_token"):
            raw[field] = self.cipher.encrypt(raw[field].encode()).decode() if raw[field] else ""
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (json.dumps(raw),))
        return settings

    def commit_snapshot(self, snapshot: ScanSnapshot, settings: Settings):
        if not snapshot.complete:
            raise ValueError("Snapshot chưa hoàn chỉnh")
        namespace = hashlib.sha256(settings.username.encode()).hexdigest()
        now = time.time()
        observations = merge_orders(snapshot.orders)
        scope = {"stores":settings.stores, "days":settings.lookback_days}
        count = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload FROM scopes WHERE namespace=?", (namespace,)).fetchone()
            old_scope = json.loads(previous["payload"]) if previous else None
            for order in observations:
                if settings.stores and order.shop_code not in settings.stores:
                    continue
                key = (namespace, order.shop_code, order.shopify_order_id)
                exists = db.execute("SELECT 1 FROM orders WHERE namespace=? AND shop=? AND order_id=?", key).fetchone()
                payload = asdict(order)
                payload["amount"] = str(order.amount)
                data = json.dumps(payload, ensure_ascii=False)
                db.execute("""INSERT INTO orders VALUES(?,?,?,?,?,?)
                    ON CONFLICT(namespace,shop,order_id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen""",
                    (*key, data, now, now))
                covered = old_scope is not None and (not old_scope["stores"] or order.shop_code in old_scope["stores"])
                # Only baseline newly expanded dates; existing overlap still alerts.
                if covered and old_scope["days"] is not None and (
                    settings.lookback_days is None or settings.lookback_days > old_scope["days"]
                ):
                    floor = (datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date() - timedelta(days=old_scope["days"])).isoformat()
                    covered = bool(order.purchase_date and order.purchase_date >= floor)
                if not exists and covered:
                    event_key = json.dumps([*key, "new_order"])
                    db.execute("""INSERT OR IGNORE INTO outbox
                        (event_key,payload,chat_id,topic_id,created_at,updated_at) VALUES(?,?,?,?,?,?)""",
                        (event_key,data,settings.chat_id,settings.topic_id,now,now))
                    count += 1
            db.execute("INSERT OR REPLACE INTO scopes VALUES(?,?)", (namespace,json.dumps(scope)))
        return {"new_orders":count, "baseline_initialized":previous is None}

    def start_scan(self):
        with self.connect() as db:
            return db.execute("INSERT INTO scans(started_at,status) VALUES(?,'running')",(time.time(),)).lastrowid

    def finish_scan(self, scan_id, status, pages=0, orders=0, new_orders=0, error=None):
        with self.connect() as db:
            db.execute("UPDATE scans SET finished_at=?,status=?,pages=?,orders_count=?,new_orders=?,error=? WHERE id=?",
                       (time.time(),status,pages,orders,new_orders,error,scan_id))

    def recover_sending(self):
        with self.connect() as db:
            db.execute("UPDATE outbox SET status='uncertain',error='Server khởi động lại khi đang gửi; kiểm tra Telegram trước khi thử lại.' WHERE status='sending'")
            db.execute("UPDATE scans SET status='failed',finished_at=?,error='Lượt quét bị gián đoạn bởi restart.' WHERE status='running'", (time.time(),))

    def claim_event(self):
        settings = self.settings()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cooldown = db.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
            if cooldown and cooldown[0] > time.time():
                return None
            if settings.chat_id:
                db.execute("UPDATE outbox SET chat_id=?,topic_id=? WHERE chat_id='' AND status='pending'",
                           (settings.chat_id,settings.topic_id))
            row = db.execute("SELECT * FROM outbox WHERE status IN ('pending','retrying') AND chat_id!='' AND next_retry<=? ORDER BY id LIMIT 1",(time.time(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?", (time.time(),row["id"]))
            result = dict(row)
            result["attempts"] += 1
            result["payload"] = json.loads(result["payload"])
            return result

    def finish_event(self, event, result):
        state = result.status
        if state == "retryable":
            state = "failed" if event["attempts"] >= 8 else "retrying"
        retry = max(result.retry_after or 0, min(3600, 5 * 2 ** min(event["attempts"], 10)))
        with self.connect() as db:
            if result.retry_after:
                db.execute("INSERT INTO cooldown VALUES(1,?) ON CONFLICT(id) DO UPDATE SET until=MAX(until,excluded.until)",
                           (time.time()+result.retry_after,))
            db.execute("UPDATE outbox SET status=?,message_id=?,error=?,next_retry=?,updated_at=? WHERE id=?",
                       (state,result.message_id,result.error,time.time()+retry,time.time(),event["id"]))

    def retry_event(self, event_id):
        with self.connect() as db:
            cur = db.execute("UPDATE outbox SET status='pending',attempts=0,next_retry=0,error=NULL WHERE id=? AND status IN ('failed','uncertain')",(event_id,))
            return cur.rowcount == 1

    def history(self, limit=100):
        with self.connect() as db:
            scans = [dict(r) for r in db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?",(limit,))]
            events = [dict(r) for r in db.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?",(limit,))]
            orders = [dict(r) for r in db.execute("SELECT payload,first_seen,last_seen FROM orders ORDER BY first_seen DESC LIMIT ?",(limit,))]
        for row in events + orders:
            row["payload"] = json.loads(row["payload"])
        return {"scans":scans, "notifications":events, "orders":orders}

    def summary(self):
        with self.connect() as db:
            counts = {r["status"]:r["n"] for r in db.execute("SELECT status,COUNT(*) n FROM outbox GROUP BY status")}
            last = db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            success = db.execute("SELECT finished_at FROM scans WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
            total = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        return {"queue":counts, "last_scan":dict(last) if last else None,
                "last_success":success[0] if success else None, "orders_count":total}
