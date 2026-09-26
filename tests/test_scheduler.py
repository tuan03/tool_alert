import asyncio
from cryptography.fernet import Fernet
from app.db import Repository
from app.models import ScanSnapshot
from app.scheduler import Scheduler

class BlockingCrawler:
    def __init__(self):
        self.gate=asyncio.Event()
        self.calls=0
    async def scan(self,s):
        self.calls+=1
        await self.gate.wait()
        return ScanSnapshot([])

async def test_manual_busy_and_failure_releases_lock(tmp_path):
    r=Repository(tmp_path/"db",Fernet.generate_key().decode())
    r.update_settings({"username":"u","password":"p"})
    c=BlockingCrawler()
    sc=Scheduler(r,c)
    assert sc.trigger()
    await asyncio.sleep(0)
    assert not sc.trigger()
    c.gate.set()
    await sc.current
    assert c.calls == 1 and not sc.busy
    assert r.summary()["last_scan"]["status"] == "success"

async def test_interval_and_pause_no_overlap(tmp_path):
    r=Repository(tmp_path/"db",Fernet.generate_key().decode())
    r.update_settings({"username":"u","password":"p","enabled":True})
    c=BlockingCrawler()
    clock=[1000.0]
    sc=Scheduler(r,c,clock=lambda:clock[0])
    await sc.tick()
    await asyncio.sleep(0)
    assert sc.busy
    clock[0]=1500.0
    await sc.tick()
    assert c.calls == 1
    c.gate.set()
    await sc.current
    assert sc.next_due == 1620
    r.update_settings({"interval_seconds":60})
    sc.settings_changed()
    assert sc.next_due == 1560
    r.update_settings({"enabled":False})
    clock[0]=2000
    await sc.tick()
    assert c.calls == 1
