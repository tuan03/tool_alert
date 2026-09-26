import asyncio
import time
from app.crawler.parser import SourceError


class Scheduler:
    def __init__(self, repo, crawler, clock=time.monotonic):
        self.repo, self.crawler, self.clock = repo, crawler, clock
        self.next_due = clock()
        self.current = None
        self.busy = False
        self.last_error = None

    def settings_changed(self):
        self.next_due = self.clock() + self.repo.settings().interval_seconds

    def trigger(self):
        if self.busy:
            return False
        self.busy = True
        self.current = asyncio.create_task(self._run())
        return True

    async def _run(self):
        started = self.clock()
        scan_id = self.repo.start_scan()
        try:
            settings = self.repo.settings()
            if not settings.username or not settings.password:
                raise SourceError("Chưa cấu hình tài khoản BKTeam.")
            snapshot = await self.crawler.scan(settings)
            result = self.repo.commit_snapshot(snapshot,settings)
            self.repo.finish_scan(scan_id,"success",snapshot.pages,len(snapshot.orders),result["new_orders"])
            self.last_error = None
        except asyncio.CancelledError:
            self.repo.finish_scan(scan_id,"failed",error="Lượt quét dừng khi server tắt.")
            raise
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc,SourceError) else "Lỗi nội bộ khi quét. Snapshot không được xác nhận."
            self.repo.finish_scan(scan_id,"failed",error=self.last_error)
        finally:
            interval = self.repo.settings().interval_seconds
            scheduled = started + interval
            self.next_due = scheduled if scheduled > self.clock() else self.clock()+interval
            self.busy = False

    async def tick(self):
        settings = self.repo.settings()
        if settings.enabled and settings.username and settings.password and not self.busy and self.clock() >= self.next_due:
            self.trigger()

    async def loop(self):
        while True:
            await self.tick()
            await asyncio.sleep(1)

    async def stop(self):
        if self.current and not self.current.done():
            self.current.cancel()
            await asyncio.gather(self.current,return_exceptions=True)

    def status(self):
        s = self.repo.settings()
        return {**self.repo.summary(),"enabled":s.enabled,"running":self.busy,
                "interval_seconds":s.interval_seconds,"configured":bool(s.username and s.password),
                "next_scan_at":time.time()+max(0,self.next_due-self.clock()) if s.enabled and not self.busy else None,
                "last_error":self.last_error}
