import re
from decimal import Decimal
from datetime import datetime
from bs4 import BeautifulSoup
from app.models import OrderObservation


class SourceError(RuntimeError):
    pass


def merge_orders(orders):
    unique = {}
    for order in orders:
        key = (order.shop_code, order.shopify_order_id)
        if key in unique:
            old = unique[key]
            if old.amount != order.amount or old.currency != order.currency:
                raise SourceError("Grand Total không nhất quán giữa các hàng cùng đơn.")
            old.source_item_ids = sorted(set(old.source_item_ids + order.source_item_ids))
        else:
            unique[key] = order
    return list(unique.values())


def parse_orders(html: str) -> list[OrderObservation]:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one("#header_order_shopify")
    if not soup.select_one("#shopify-order-search-form") or not header:
        raise SourceError("Không tìm thấy bảng đơn; phiên hết hạn hoặc cấu trúc trang đã đổi.")
    cells = header.find_all(["td", "th"], recursive=False)
    names = [c.get_text(" ", strip=True) for c in cells]
    needed = ["Shopify Store", "Order ID", "Grand Total"]
    if any(names.count(n) != 1 for n in needed):
        raise SourceError("Header bảng đơn không đúng cấu trúc.")
    indexes = {n: names.index(n) for n in needed}
    table = header.find_parent("table")
    orders = []
    for check in table.select(".shopify-order-check"):
        row = check.find_parent("tr")
        values = row.find_all(["td", "th"], recursive=False)
        if len(values) != len(cells):
            raise SourceError("Số cột dữ liệu thay đổi.")
        shop, order_id, internal = (str(check.get(k, "")).strip() for k in
                                   ("data-shop-code", "data-order-number", "value"))
        if not shop or not order_id or not internal:
            raise SourceError("Thiếu định danh đơn hoặc store.")
        raw = values[indexes["Grand Total"]].get_text(strip=True)
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", raw):
            raise SourceError("Grand Total rỗng hoặc không hợp lệ.")
        store_cell = values[indexes["Shopify Store"]]
        store = store_cell.find("a")
        store_name = store.get_text(" ", strip=True) if store else store_cell.get_text(" ", strip=True)
        label_cell = values[indexes["Order ID"]]
        label = (label_cell.find("a") or label_cell).get_text("\n", strip=True).split("\n")[0]
        purchase_date = None
        if "Purchase" in names:
            date_text = values[names.index("Purchase")].get_text(strip=True)
            if date_text:
                try:
                    purchase_date = datetime.strptime(date_text, "%d-%m-%Y").date().isoformat()
                except ValueError:
                    raise SourceError("Ngày mua hàng không hợp lệ.") from None
        orders.append(OrderObservation(shop, order_id, [internal], store_name, label,
                                       Decimal(raw.replace(",", "")), purchase_date=purchase_date))
    return merge_orders(orders)


def pagination_offsets(html: str) -> set[int]:
    soup = BeautifulSoup(html, "html.parser")
    nav = soup.select_one("#pagenav_bar")
    if nav is None:
        raise SourceError("Không tìm thấy điều hướng trang.")
    offsets = set()
    for a in nav.select("a"):
        script = (a.get("href") or "") + " " + (a.get("onclick") or "")
        match = re.search(r"(?:[?&])offset=(\d+)", script)
        if not match:
            raise SourceError("Điều hướng trang chưa được hỗ trợ; dừng để tránh bỏ sót đơn.")
        offsets.add(int(match.group(1)))
    return offsets
