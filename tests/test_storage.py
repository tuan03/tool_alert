from decimal import Decimal
import pytest
from cryptography.fernet import Fernet
from app.db import Repository
from app.models import Settings, OrderObservation, ScanSnapshot

KEY = Fernet.generate_key().decode()
def order(n, shop="S"):
    return OrderObservation(shop, str(n), [str(n)], "Store", f"ORDER-{n}", Decimal("27.81"))
def repo(tmp_path):
    return Repository(tmp_path / "app.db", KEY)

def test_settings_encrypt_blank_preserve_restart(tmp_path):
    r = repo(tmp_path)
    r.update_settings({"username":"u","password":"secret-password", "bot_token":"123:abcdefghijk","chat_id":"-123"})
    r.update_settings({"password":"","interval_seconds":60})
    assert repo(tmp_path).settings().password == "secret-password"
    assert repo(tmp_path).settings().interval_seconds == 60
    assert "secret-password" not in str(r.public_settings())
    assert "abcdefghijk" not in str(r.public_settings())
    assert b"secret-password" not in (tmp_path/"app.db").read_bytes()
    with pytest.raises(ValueError):
        r.update_settings({"interval_seconds":29})
    with pytest.raises(RuntimeError):
        Repository(tmp_path/"app.db", Fernet.generate_key().decode()).settings()

def test_baseline_restart_atomic_and_duplicate(tmp_path):
    r = repo(tmp_path)
    s = Settings(username="u", chat_id="-123")
    assert r.commit_snapshot(ScanSnapshot([order(1)]), s)["new_orders"] == 0
    assert r.commit_snapshot(ScanSnapshot([order(1),order(2),order(2)]), s)["new_orders"] == 1
    assert repo(tmp_path).commit_snapshot(ScanSnapshot([order(2)]), s)["new_orders"] == 0
    assert len(r.history()["notifications"]) == 1
    with pytest.raises(ValueError):
        r.commit_snapshot(ScanSnapshot([order(3)], complete=False), s)
    assert r.commit_snapshot(ScanSnapshot([order(3)]), s)["new_orders"] == 1

def test_failed_first_snapshot_and_namespace(tmp_path):
    r = repo(tmp_path)
    with pytest.raises(ValueError):
        r.commit_snapshot(ScanSnapshot([order(1)],complete=False), Settings(username="u"))
    assert r.commit_snapshot(ScanSnapshot([order(1)]), Settings(username="u"))["baseline_initialized"]
    assert r.commit_snapshot(ScanSnapshot([order(1)]), Settings(username="other"))["new_orders"] == 0

def test_expanding_stores_baselines_only_new_scope(tmp_path):
    r = repo(tmp_path)
    r.commit_snapshot(ScanSnapshot([order(1)]), Settings(username="u",stores=["S"]))
    result = r.commit_snapshot(ScanSnapshot([order(1),order(2),order(3,"T")]), Settings(username="u",stores=[]))
    assert result["new_orders"] == 1

def test_destination_snapshot_and_claim_recovery(tmp_path):
    r = repo(tmp_path)
    s = Settings(username="u", chat_id="-111")
    r.commit_snapshot(ScanSnapshot([]),s)
    r.commit_snapshot(ScanSnapshot([order(1)]),s)
    r.update_settings({"chat_id":"-222"})
    event = r.claim_event()
    assert event["chat_id"] == "-111"
    assert r.claim_event() is None
    r.recover_sending()
    assert r.history()["notifications"][0]["status"] == "uncertain"

def test_unassigned_destination_waits_then_binds_once(tmp_path):
    r=repo(tmp_path)
    s=Settings(username="u")
    r.commit_snapshot(ScanSnapshot([]),s)
    r.commit_snapshot(ScanSnapshot([order(1)]),s)
    assert r.claim_event() is None
    r.update_settings({"chat_id":"-555","topic_id":8})
    event=r.claim_event()
    assert event["chat_id"] == "-555" and event["topic_id"] == 8

def test_rate_limit_defers_entire_queue(tmp_path):
    from app.telegram import DeliveryResult
    r=repo(tmp_path)
    s=Settings(username="u",chat_id="-1")
    r.commit_snapshot(ScanSnapshot([]),s)
    r.commit_snapshot(ScanSnapshot([order(1),order(2)]),s)
    event=r.claim_event()
    r.finish_event(event,DeliveryResult("retryable",retry_after=60,error="rate"))
    assert repo(tmp_path).claim_event() is None

def test_transaction_rolls_back_order_when_outbox_insert_fails(tmp_path):
    import sqlite3
    r=repo(tmp_path)
    s=Settings(username="u",chat_id="-1")
    r.commit_snapshot(ScanSnapshot([]),s)
    with r.connect() as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        r.commit_snapshot(ScanSnapshot([order(1)]),s)
    assert r.summary()["orders_count"] == 0
    with r.connect() as db:
        db.execute("DROP TRIGGER reject_event")
    assert r.commit_snapshot(ScanSnapshot([order(1)]),s)["new_orders"] == 1

def test_expanding_dates_does_not_silence_new_orders_in_old_window(tmp_path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    r=repo(tmp_path)
    r.commit_snapshot(ScanSnapshot([]),Settings(username="u",lookback_days=30))
    recent,old=order(1),order(2)
    recent.purchase_date=today.isoformat()
    old.purchase_date=(today-timedelta(days=100)).isoformat()
    result=r.commit_snapshot(ScanSnapshot([recent,old]),Settings(username="u",lookback_days=None))
    assert result["new_orders"] == 1
    assert r.history()["notifications"][0]["payload"]["shopify_order_id"] == "1"
