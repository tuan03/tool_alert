import pytest
from app.crawler.client import Crawler
from app.models import Settings
from app.crawler.parser import SourceError
from tests.helpers import html

def test_request_policy_keeps_login_jquery_but_blocks_tracking_writes():
    from app.crawler.client import allowed_request
    assert allowed_request("https://ajax.googleapis.com/ajax/libs/jquery/1.12.4/jquery.min.js","GET","script")
    assert allowed_request("https://bkteam.top/dungvuong-admin/ajax.php?page=login","POST","xhr")
    assert not allowed_request("https://bkteam.top/dungvuong-admin/ajax.php?page=presence","POST","xhr")
    assert not allowed_request("https://evil.example/x.js","GET","script")

class PagesCrawler(Crawler):
    def __init__(self,pages):
        super().__init__()
        self.pages=pages
        self.visited=[]
    async def fetch_html(self, settings, offset):
        self.visited.append(offset)
        result=self.pages[offset]
        if isinstance(result,Exception): raise result
        return result

async def test_all_pages_and_dedupe():
    c=PagesCrawler({0:html([("1","1","S","10")],paging='<a href="?offset=37">2</a>'),
                    37:html([("1","1","S","10"),("2","2","S","20")])})
    snap=await c.scan(Settings())
    assert c.visited == [0,37]
    assert len(snap.orders) == 2 and snap.complete

async def test_partial_page_fails_whole_snapshot():
    c=PagesCrawler({0:html(paging='<a href="?offset=37">2</a>'),37:SourceError("page failed")})
    with pytest.raises(SourceError): await c.scan(Settings())

async def test_different_items_same_order_can_cross_page_boundary():
    c=PagesCrawler({0:html([("item1","1","S","10")],paging='<a href="?offset=37">2</a>'),
                   37:html([("item2","1","S","10")])})
    result=await c.scan(Settings())
    assert len(result.orders) == 1
    assert result.orders[0].source_item_ids == ["item1","item2"]

async def test_same_internal_id_with_different_products_on_separate_pages():
    first=html([("1","1","S","10")],paging='<a href="?offset=37">2</a>').replace("ORDER-1","PRODUCT-A ORDER-1")
    second=html([("1","1","S","10")]).replace("ORDER-1","PRODUCT-B ORDER-1")
    result=await PagesCrawler({0:first,37:second}).scan(Settings())
    assert len(result.orders) == 1

async def test_repeated_content_on_different_page_fails():
    c=PagesCrawler({0:html([("1","1","S","10")],paging='<a href="?offset=37">2</a>'),
                   37:html([("1","1","S","10")])})
    with pytest.raises(SourceError): await c.scan(Settings())

async def test_login_uses_visible_result_when_ajax_body_disappears():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            page.set_default_timeout(3000)
            await page.set_content("""<input id="Username"><input id="Password"><input type="submit" onclick="document.body.innerHTML='<form id=shopify-order-search-form></form><table><tr id=header_order_shopify></tr></table>'">""")
            c = Crawler()
            c.page = page
            await c._login(Settings(username="u",password="p"))
            assert await page.locator("#header_order_shopify").count() == 1
        finally:
            await browser.close()
