import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
import portalocker
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from app.auth import Auth
from app.config import Bootstrap
from app.db import Repository
from app.crawler.client import Crawler
from app.crawler.parser import SourceError
from app.scheduler import Scheduler
from app.telegram import TelegramClient, send_one

ASSETS = Path(__file__).parent


def create_app(config: Bootstrap | None = None, background=True):
    config = config or Bootstrap.from_env()
    repo = Repository(config.data_dir/"alerts.sqlite3",config.encryption_key)
    auth = Auth(repo,config.admin_token)
    crawler = Crawler()
    scheduler = Scheduler(repo,crawler)
    # HTTPX INFO logs include URL paths containing bot tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app):
        lock = None
        tasks = []
        loop = asyncio.get_running_loop()
        outbox_wake = asyncio.Event()
        repo.on_outbox_change = lambda: loop.call_soon_threadsafe(outbox_wake.set)
        if background:
            lock = portalocker.Lock(str(config.data_dir/"server.lock"),timeout=0)
            try:
                lock.acquire()
            except portalocker.LockException:
                raise RuntimeError("Đã có một server dùng DATA_DIR này. Chỉ chạy 1 worker.") from None
            repo.recover_sending()
        async with httpx.AsyncClient(follow_redirects=False) as http:
            app.state.telegram = TelegramClient(http)
            async def sender_loop():
                while True:
                    outbox_wake.clear()
                    try:
                        while repo.settings().bot_token and await send_one(repo,app.state.telegram):
                            pass
                    except Exception:
                        # A claimed event is ambiguous if an unexpected failure interrupts delivery.
                        with repo.connect() as db:
                            db.execute("UPDATE outbox SET status='uncertain',error='Gửi bị gián đoạn; kiểm tra Telegram trước khi thử lại.' WHERE status='sending'")
                    delay = repo.next_outbox_delay()
                    if delay is None:
                        await outbox_wake.wait()
                    elif delay > 0:
                        try:
                            await asyncio.wait_for(outbox_wake.wait(), timeout=delay)
                        except TimeoutError:
                            pass
            if background:
                tasks = [asyncio.create_task(scheduler.loop()),asyncio.create_task(sender_loop())]
            try:
                yield
            finally:
                repo.on_outbox_change = None
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                await scheduler.stop()
                await crawler.close()
                repo.close()
                if lock:
                    lock.release()

    app = FastAPI(title="BKTeam Alerts",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.repo, app.state.scheduler, app.state.auth = repo,scheduler,auth
    templates = Jinja2Templates(directory=str(ASSETS/"templates"))
    app.mount("/static",StaticFiles(directory=str(ASSETS/"static")),name="static")

    @app.middleware("http")
    async def security_headers(request, call_next):
        if int(request.headers.get("content-length","0") or "0") > 16384:
            return JSONResponse({"detail":"Yêu cầu quá lớn."},status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
        return response

    async def json_body(request):
        try:
            body = await request.json()
            if not isinstance(body,dict):
                raise ValueError()
            return body
        except (ValueError,TypeError):
            raise HTTPException(400,"JSON không hợp lệ.") from None

    def validated_settings(changes):
        try:
            return repo.merged_settings(changes)
        except (ValueError,ValidationError):
            raise HTTPException(422,"Cấu hình không hợp lệ. Kiểm tra chu kỳ, bot token, chat ID, topic và store.") from None

    @app.get("/health")
    async def health():
        return {"status":"ok"}

    @app.get("/login",response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request=request,name="login.html",context={})

    @app.post("/auth/login")
    async def login(request: Request):
        body = await json_body(request)
        token = body.get("token","")
        if not isinstance(token,str) or len(token)>1000:
            raise HTTPException(401,"Token không hợp lệ.")
        session,csrf = auth.login(request,token)
        response = JSONResponse({"csrf":csrf})
        response.set_cookie("session",session,httponly=True,secure=request.url.scheme=="https",samesite="strict",max_age=43200)
        return response

    @app.post("/auth/logout")
    async def logout(request: Request,session=Depends(auth.require)):
        auth.logout(request)
        response=JSONResponse({"ok":True})
        response.delete_cookie("session")
        return response

    @app.get("/",response_class=HTMLResponse)
    async def home(request: Request):
        try:
            session = auth.require(request)
        except HTTPException:
            return RedirectResponse("/login",status_code=303)
        return templates.TemplateResponse(request=request,name="dashboard.html",context={"csrf":session["csrf"]})

    @app.get("/api/settings")
    async def get_settings(session=Depends(auth.require)):
        return repo.public_settings()

    @app.put("/api/settings")
    async def save_settings(request: Request,session=Depends(auth.require)):
        changes = await json_body(request)
        settings = validated_settings(changes)
        if settings.enabled and (not settings.username or not settings.password or not settings.bot_token or not settings.chat_id):
            raise HTTPException(422,"Cần đủ tài khoản nguồn, bot token và chat ID trước khi bật tự động.")
        repo.update_settings(changes)
        scheduler.settings_changed()
        return repo.public_settings()

    @app.get("/api/status")
    async def status(session=Depends(auth.require)):
        return scheduler.status()

    @app.get("/api/history")
    async def history(session=Depends(auth.require)):
        return repo.history()

    @app.post("/api/scan")
    async def scan(session=Depends(auth.require)):
        s = repo.settings()
        if not s.username or not s.password:
            raise HTTPException(400,"Nhập và lưu tài khoản BKTeam trước.")
        if not scheduler.trigger():
            raise HTTPException(409,"Đang quét, vui lòng chờ.")
        return {"ok":True}

    @app.post("/api/source/test")
    async def source_test(request: Request,session=Depends(auth.require)):
        settings = validated_settings(await json_body(request))
        if not settings.username or not settings.password:
            raise HTTPException(400,"Chưa nhập tài khoản BKTeam.")
        probe = Crawler()
        try:
            await asyncio.wait_for(probe.test_login(settings),timeout=60)
            return {"ok":True,"message":"Đăng nhập và đọc bảng đơn thành công."}
        except (SourceError,TimeoutError) as exc:
            message = str(exc) if isinstance(exc,SourceError) else "Hết thời gian kiểm tra nguồn."
            raise HTTPException(400,message) from None
        finally:
            await probe.close()

    @app.post("/api/telegram/test")
    async def telegram_test(request: Request,session=Depends(auth.require)):
        s = validated_settings(await json_body(request))
        result = await app.state.telegram.send(s.bot_token,s.chat_id,s.topic_id,
                         "✅ BKTeam Alerts: kết nối Telegram thành công. Đây là tin thử từ UI.")
        return {"status":result.status,"message_id":result.message_id,"error":result.error}

    @app.post("/api/notifications/{event_id}/retry")
    async def retry(event_id: int,session=Depends(auth.require)):
        if not repo.retry_event(event_id):
            raise HTTPException(409,"Chỉ thử lại tin failed hoặc uncertain.")
        return {"ok":True}

    return app
