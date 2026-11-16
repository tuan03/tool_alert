import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet, InvalidToken
from app.models import Settings, ScanSnapshot
from app.crawler.parser import merge_orders

MAX_RETAINED_SCANS = 500


class Repository:
    def __init__(self, path: Path, encryption_key: str, on_outbox_change=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(encryption_key.encode())
        self.on_outbox_change = on_outbox_change
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._settings_cache: Settings | None = None
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA temp_store=MEMORY;
                PRAGMA busy_timeout=10000;
                PRAGMA wal_autocheckpoint=1000;
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
                CREATE INDEX IF NOT EXISTS idx_outbox_status_retry ON outbox(status, next_retry, id);
                CREATE INDEX IF NOT EXISTS idx_scans_status_id ON scans(status, id DESC);
            """)
            pruned = self._prune_scans(db)
        if pruned > 1000:
            with self._lock:
                if self._conn is not None:
                    self._conn.execute("VACUUM")
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.settings()  # Validate encryption before background work starts.

    def _notify_outbox(self):
        if callable(self.on_outbox_change):
            try:
                self.on_outbox_change()
            except Exception:
                pass

    def _prune_scans(self, db: sqlite3.Connection) -> int:
        cur = db.execute(
            """
            DELETE FROM scans
            WHERE id <= COALESCE(
                (SELECT id FROM scans ORDER BY id DESC LIMIT 1 OFFSET ?),
                0
            )
            AND id != COALESCE(
                (SELECT id FROM scans WHERE status='success' ORDER BY id DESC LIMIT 1),
                0
            )
            """,
            (MAX_RETAINED_SCANS,),
        )
        return cur.rowcount

    @contextmanager
    def connect(self):
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("PRAGMA temp_store=MEMORY")
                self._conn.execute("PRAGMA busy_timeout=10000")
                self._conn.execute("PRAGMA wal_autocheckpoint=1000")
            with self._conn:
                yield self._conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def settings(self) -> Settings:
        with self._lock:
            if self._settings_cache is not None:
                return self._settings_cache.model_copy(deep=True)
            with self.connect() as db:
                row = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
            if not row:
                self._settings_cache = Settings()
                return self._settings_cache.model_copy(deep=True)
            raw = json.loads(row["payload"])
            try:
                for field in ("password", "bot_token"):
                    raw[field] = self.cipher.decrypt(raw[field].encode()).decode() if raw.get(field) else ""
            except InvalidToken:
                raise RuntimeError("Không giải mã được cấu hình. Khôi phục SETTINGS_ENCRYPTION_KEY đúng.") from None
            self._settings_cache = Settings(**raw)
            return self._settings_cache.model_copy(deep=True)

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
        with self._lock:
            settings = self.merged_settings(changes)
            raw = settings.model_dump()
            for field in ("password", "bot_token"):
                raw[field] = self.cipher.encrypt(raw[field].encode()).decode() if raw[field] else ""
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (json.dumps(raw),))
            self._settings_cache = settings.model_copy(deep=True)
        if settings.bot_token and settings.chat_id:
            self._notify_outbox()
        return settings

    def commit_snapshot(self, snapshot: ScanSnapshot, settings: Settings):
        if not snapshot.complete:
            raise ValueError("Snapshot chưa hoàn chỉnh")
        namespace = hashlib.sha256(settings.username.encode()).hexdigest()
        now = time.time()
        observations = [
            o for o in merge_orders(snapshot.orders)
            if not settings.stores or o.shop_code in settings.stores
        ]
        scope = {"stores":settings.stores, "days":settings.lookback_days}
        count = 0
        with self.connect() as db:
            previous = db.execute("SELECT payload FROM scopes WHERE namespace=?", (namespace,)).fetchone()
            old_scope = json.loads(previous["payload"]) if previous else None
            existing_rows = {
                (r["shop"], r["order_id"]): (r["payload"], r["last_seen"])
                for r in db.execute(
                    "SELECT shop, order_id, payload, last_seen FROM orders WHERE namespace=?",
                    (namespace,),
                )
            }
            pending_writes = []
            for order in observations:
                payload = asdict(order)
                payload["amount"] = str(order.amount)
                data = json.dumps(payload, ensure_ascii=False)
                prev = existing_rows.get((order.shop_code, order.shopify_order_id))
                exists = prev is not None
                needs_order_write = (not exists) or (prev[0] != data) or (now - prev[1] >= 3600)
                if needs_order_write:
                    pending_writes.append((order, data, exists))
            if not pending_writes and old_scope == scope:
                return {"new_orders": 0, "baseline_initialized": False}
            db.execute("BEGIN IMMEDIATE")
            for order, data, exists in pending_writes:
                key = (namespace, order.shop_code, order.shopify_order_id)
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
            if old_scope != scope:
                db.execute("INSERT OR REPLACE INTO scopes VALUES(?,?)", (namespace,json.dumps(scope)))
        if count > 0:
            self._notify_outbox()
        return {"new_orders":count, "baseline_initialized":previous is None}

    def start_scan(self):
        with self.connect() as db:
            return db.execute("INSERT INTO scans(started_at,status) VALUES(?,'running')",(time.time(),)).lastrowid

    def finish_scan(self, scan_id, status, pages=0, orders=0, new_orders=0, error=None):
        with self.connect() as db:
            db.execute("UPDATE scans SET finished_at=?,status=?,pages=?,orders_count=?,new_orders=?,error=? WHERE id=?",
                       (time.time(),status,pages,orders,new_orders,error,scan_id))
            self._prune_scans(db)

    def recover_sending(self):
        with self.connect() as db:
            db.execute("UPDATE outbox SET status='uncertain',error='Server khởi động lại khi đang gửi; kiểm tra Telegram trước khi thử lại.' WHERE status='sending'")
            db.execute("UPDATE scans SET status='failed',finished_at=?,error='Lượt quét bị gián đoạn bởi restart.' WHERE status='running'", (time.time(),))

    def claim_event(self):
        settings = self.settings()
        now = time.time()
        with self.connect() as db:
            cooldown = db.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
            if cooldown and cooldown[0] > now:
                return None
            needs_binding = bool(
                settings.chat_id
                and db.execute("SELECT 1 FROM outbox WHERE status='pending' AND chat_id='' LIMIT 1").fetchone()
            )
            has_due = bool(
                db.execute(
                    "SELECT 1 FROM outbox WHERE status IN ('pending','retrying') AND chat_id!='' AND next_retry<=? LIMIT 1",
                    (now,),
                ).fetchone()
            )
            if not needs_binding and not has_due:
                return None
            db.execute("BEGIN IMMEDIATE")
            if needs_binding:
                db.execute("UPDATE outbox SET chat_id=?,topic_id=? WHERE chat_id='' AND status='pending'",
                           (settings.chat_id,settings.topic_id))
            row = db.execute("SELECT * FROM outbox WHERE status IN ('pending','retrying') AND chat_id!='' AND next_retry<=? ORDER BY id LIMIT 1",(now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?", (now,row["id"]))
            result = dict(row)
            result["attempts"] += 1
            result["payload"] = json.loads(result["payload"])
            return result

    def next_outbox_delay(self) -> float | None:
        settings = self.settings()
        if not settings.bot_token:
            return None
        now = time.time()
        with self.connect() as db:
            if settings.chat_id:
                row = db.execute(
                    "SELECT MIN(next_retry) FROM outbox WHERE status IN ('pending','retrying') AND (chat_id!='' OR status='pending')"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT MIN(next_retry) FROM outbox WHERE status IN ('pending','retrying') AND chat_id!=''"
                ).fetchone()
            if not row or row[0] is None:
                return None
            cooldown = db.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
            wake_at = max(float(row[0]), float(cooldown[0]) if cooldown else 0.0)
            return max(0.0, wake_at - now)

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
            ok = cur.rowcount == 1
        if ok:
            self._notify_outbox()
        return ok

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
