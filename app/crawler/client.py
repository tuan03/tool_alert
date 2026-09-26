import asyncio
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from bs4 import BeautifulSoup
from app.models import Settings, ScanSnapshot
from app.crawler.parser import parse_orders, pagination_offsets, merge_orders, SourceError

BASE = "https://bkteam.top/dungvuong-admin/"
TARGET = BASE + "admin.php"


def allowed_request(url, method, resource_type):
    parsed = urlparse(url)
    if resource_type in ("image", "media", "font"):
        return False
    if parsed.hostname in ("ajax.googleapis.com", "maxcdn.bootstrapcdn.com"):
        return method == "GET" and resource_type in ("script", "stylesheet")
    if parsed.hostname != "bkteam.top":
        return False
    if "/ajax.php" in parsed.path:
        return parsed.query == "page=login"
    return method == "GET"


class Crawler:
    def __init__(self):
        self.driver = self.browser = self.context = self.page = None
        self.credentials = None
        self.lock = asyncio.Lock()

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.driver:
            await self.driver.stop()
        self.driver = self.browser = self.context = self.page = None
        self.credentials = None

    async def _ensure(self, settings):
        fingerprint = hashlib.sha256((settings.username+"\0"+settings.password).encode()).hexdigest()
        if self.credentials != fingerprint:
            await self.close()
        if self.page is None:
            self.driver = await async_playwright().start()
            self.browser = await self.driver.chromium.launch(headless=True)
            self.context = await self.browser.new_context()
            self.context.set_default_timeout(30000)
            await self.context.route("**/*", self._route)
            self.page = await self.context.new_page()
            self.credentials = fingerprint

    async def _route(self, route):
        request = route.request
        # Do not load customer/product imagery or run background presence writes.
        if not allowed_request(request.url, request.method, request.resource_type):
            return await route.abort()
        return await route.continue_()

    async def _login(self, settings):
        if not settings.username or not settings.password:
            raise SourceError("Chưa cấu hình tài khoản BKTeam.")
        await self.page.locator("#Username").fill(settings.username)
        await self.page.locator("#Password").fill(settings.password)
        await self.page.locator('input[type="submit"]').click()
        # The site navigates immediately; Chromium may evict the AJAX response body.
        await self.page.wait_for_function("""() => Boolean(
            document.querySelector('#shopify-order-search-form') ||
            (document.querySelector('#login_message')?.innerText || '').trim()
        )""")
        if not await self.page.locator("#shopify-order-search-form").count():
            raise SourceError("Đăng nhập BKTeam thất bại. Kiểm tra tài khoản/mật khẩu.")

    def url(self, settings, offset):
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        params = {"page":"manager_order_shopify","offset":offset,"cmd_search":"SEARCH",
                  "purchase_from":(today-timedelta(days=settings.lookback_days)).isoformat() if settings.lookback_days else "",
                  "purchase_to":today.isoformat() if settings.lookback_days else ""}
        return TARGET + "?" + urlencode(params)

    async def fetch_html(self, settings: Settings, offset: int):
        await self._ensure(settings)
        url = self.url(settings,offset)
        response = await self.page.goto(url,wait_until="domcontentloaded")
        if response is None or response.status >= 400:
            raise SourceError("Trang nguồn trả lỗi HTTP.")
        if await self.page.locator("#Username").count():
            await self._login(settings)
            await self.page.goto(url,wait_until="domcontentloaded")
        await self.page.locator("#header_order_shopify").wait_for()
        return await self.page.content()

    async def scan(self, settings: Settings) -> ScanSnapshot:
        async with self.lock:
            try:
                return await asyncio.wait_for(self._scan(settings), timeout=300)
            except (BrowserTimeout, TimeoutError):
                raise SourceError("BKTeam phản hồi quá chậm; snapshot không được lưu.") from None
            except SourceError:
                raise
            except Exception:
                await self.close()
                raise SourceError("Không đọc được BKTeam; kiểm tra kết nối hoặc Chromium.") from None

    async def _scan(self, settings):
        pending, visited, signatures, orders = {0}, set(), set(), []
        while pending:
            if len(visited) >= 1000:
                raise SourceError("Vượt 1000 trang; dừng để tránh lưu snapshot thiếu.")
            offset = min(pending)
            pending.remove(offset)
            html = await self.fetch_html(settings,offset)
            parsed = parse_orders(html)
            # Include item/SKU content, not merely order IDs: one order can span pages.
            table = BeautifulSoup(html, "html.parser").select_one("#header_order_shopify").find_parent("table")
            signature = hashlib.sha256(str(table).encode()).hexdigest() if parsed else None
            if signature and signature in signatures:
                raise SourceError("Nguồn lặp lại cùng trang; không xác nhận snapshot.")
            signatures.add(signature)
            orders.extend(parsed)
            visited.add(offset)
            pending.update(pagination_offsets(html) - visited)
        filtered = [o for o in merge_orders(orders) if not settings.stores or o.shop_code in settings.stores]
        return ScanSnapshot(filtered,pages=len(visited))

    async def test_login(self, settings):
        async with self.lock:
            try:
                html = await self.fetch_html(settings,0)
                parse_orders(html)
                return True
            except SourceError:
                raise
            except Exception:
                raise SourceError("Không kết nối/đăng nhập được BKTeam.") from None
