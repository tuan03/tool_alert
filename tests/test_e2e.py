import time
from decimal import Decimal
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.config import Bootstrap
from app.main import create_app
from app.models import OrderObservation, ScanSnapshot
from app.telegram import TelegramClient

def test_end_to_end_baseline_send_restart_no_duplicate(tmp_path):
    config=Bootstrap("long-test-access-token-for-e2e",Fernet.generate_key().decode(),tmp_path)
    class Source:
        def __init__(self): self.orders=[]
        async def scan(self,settings): return ScanSnapshot(list(self.orders))
        async def close(self): pass
    source=Source()
    delivered=[]
    def handle(request):
        delivered.append(request.content)
        return httpx.Response(200,json={"ok":True,"result":{"message_id":88}})
    http=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    def wait(c,predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            h=c.get("/api/history").json()
            if predicate(h): return h
            time.sleep(.05)
        raise AssertionError("Pipeline did not complete")
    def login(c):
        return {"X-CSRF-Token":c.post("/auth/login",json={"token":config.admin_token}).json()["csrf"]}
    app=create_app(config)
    app.state.scheduler.crawler=source
    with TestClient(app) as c:
        app.state.telegram=TelegramClient(http)
        headers=login(c)
        assert c.put("/api/settings",json={"username":"u","password":"p","bot_token":"123:abcdefghijk","chat_id":"-1"},headers=headers).status_code==200
        c.post("/api/scan",headers=headers)
        wait(c,lambda h:bool(h["scans"]) and h["scans"][0]["status"]=="success")
        assert not delivered
        source.orders=[OrderObservation("S","123",["item1","item2"],"My Store","#12",Decimal("27.81"))]
        c.post("/api/scan",headers=headers)
        h=wait(c,lambda h:bool(h["notifications"]) and h["notifications"][0]["status"]=="sent")
        assert h["notifications"][0]["message_id"]==88
        assert len(delivered)==1
        assert b"My Store" in delivered[0] and b"27.81" in delivered[0]
    app2=create_app(config)
    app2.state.scheduler.crawler=source
    with TestClient(app2) as c:
        app2.state.telegram=TelegramClient(http)
        headers=login(c)
        assert c.get("/api/settings").json()["password_configured"]
        c.post("/api/scan",headers=headers)
        wait(c,lambda h:len(h["scans"])==3 and h["scans"][0]["status"]=="success")
        assert len(c.get("/api/history").json()["notifications"])==1
        assert len(delivered)==1
