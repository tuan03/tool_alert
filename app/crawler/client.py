import asyncio
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo
import httpx
from app.models import Settings, ScanSnapshot
from app.crawler.parser import (
    ensure_soup,
    table_signature,
    parse_orders,
    pagination_offsets,
    merge_orders,
    SourceError,
)

BASE = "https://bkteam.top/dungvuong-admin/"
TARGET = BASE + "admin.php"
LOGIN_URL = BASE + "ajax.php?page=login"


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
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.client: httpx.AsyncClient | None = None
        self.credentials = None
        self.lock = asyncio.Lock()

    async def close(self):
        if self.client:
            await self.client.aclose()
        self.client = None
        self.credentials = None

    async def _ensure(self, settings: Settings):
        fingerprint = hashlib.sha256((settings.username + "\0" + settings.password).encode()).hexdigest()
        if self.credentials != fingerprint:
            await self.close()
        if self.client is None:
            self.client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=15.0),
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"},
                transport=self.transport,
            )
            self.credentials = fingerprint

    async def _login(self, settings: Settings):
        if not settings.username or not settings.password:
            raise SourceError("Chưa cấu hình tài khoản BKTeam.")
        await self._ensure(settings)
        response = await self.client.post(
            LOGIN_URL,
            data={"username": settings.username, "password": settings.password, "redirect": ""},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "index.php"},
        )
        if response.status_code >= 400:
            raise SourceError("Trang nguồn trả lỗi HTTP.")
        try:
            payload = response.json()
        except ValueError:
            raise SourceError("Phản hồi đăng nhập BKTeam không hợp lệ.") from None
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise SourceError("Đăng nhập BKTeam thất bại. Kiểm tra tài khoản/mật khẩu.")

    def url(self, settings, offset):
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        params = {"page":"manager_order_shopify","offset":offset,"cmd_search":"SEARCH",
                  "purchase_from":(today-timedelta(days=settings.lookback_days)).isoformat() if settings.lookback_days else "",
                  "purchase_to":today.isoformat() if settings.lookback_days else ""}
        return TARGET + "?" + urlencode(params)

    async def fetch_html(self, settings: Settings, offset: int):
        await self._ensure(settings)
        url = self.url(settings, offset)
        response = await self.client.get(url)
        if response.status_code >= 400:
            raise SourceError("Trang nguồn trả lỗi HTTP.")
        html = response.text
        if response.url.path.endswith("/index.php") or 'id="Username"' in html:
            await self._login(settings)
            response = await self.client.get(url)
            if response.status_code >= 400:
                raise SourceError("Trang nguồn trả lỗi HTTP.")
            html = response.text
        if "header_order_shopify" not in html:
            raise SourceError("Không tìm thấy bảng đơn; phiên hết hạn hoặc cấu trúc trang đã đổi.")
        return html

    async def scan(self, settings: Settings) -> ScanSnapshot:
        async with self.lock:
            try:
                return await asyncio.wait_for(self._scan(settings), timeout=300)
            except (httpx.TimeoutException, TimeoutError):
                raise SourceError("BKTeam phản hồi quá chậm; snapshot không được lưu.") from None
            except SourceError:
                raise
            except Exception:
                await self.close()
                raise SourceError("Không đọc được BKTeam; kiểm tra kết nối mạng.") from None

    async def _scan(self, settings):
        pending, visited, signatures, orders = {0}, set(), set(), []
        while pending:
            if len(visited) >= 1000:
                raise SourceError("Vượt 1000 trang; dừng để tránh lưu snapshot thiếu.")
            offset = min(pending)
            pending.remove(offset)
            html = await self.fetch_html(settings, offset)
            soup = ensure_soup(html)
            parsed = parse_orders(soup)
            # Include item/SKU content, not merely order IDs: one order can span pages.
            signature = table_signature(soup) if parsed else None
            if signature and signature in signatures:
                raise SourceError("Nguồn lặp lại cùng trang; không xác nhận snapshot.")
            signatures.add(signature)
            orders.extend(parsed)
            visited.add(offset)
            pending.update(pagination_offsets(soup) - visited)
        filtered = [o for o in merge_orders(orders) if not settings.stores or o.shop_code in settings.stores]
        return ScanSnapshot(filtered, pages=len(visited))

    async def test_login(self, settings):
        async with self.lock:
            try:
                html = await self.fetch_html(settings, 0)
                parse_orders(html)
                return True
            except SourceError:
                raise
            except Exception:
                await self.close()
                raise SourceError("Không kết nối/đăng nhập được BKTeam.") from None
