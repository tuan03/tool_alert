"""Local UI verification. Reads access token from .env; never prints it or sends Telegram."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright, expect
from app.config import Bootstrap

async def main():
    config=Bootstrap.from_env()
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={"width":1440,"height":1050})
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        await page.goto(f"http://127.0.0.1:{config.port}")
        await page.locator("#access-token").fill(config.admin_token)
        await page.locator("button[type=submit]").click()
        await page.wait_for_url(f"http://127.0.0.1:{config.port}/")
        await expect(page.locator("#state")).not_to_have_text("Đang tải")
        await page.screenshot(path="data/ui-desktop.png",full_page=True)
        await page.locator("#scan-now").click()
        await expect(page.locator("#status-description")).to_contain_text("Thành công",timeout=60000)
        await page.screenshot(path="data/ui-desktop.png",full_page=True)
        await page.set_viewport_size({"width":390,"height":844})
        await page.screenshot(path="data/ui-mobile.png",full_page=True)
        overflow=await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(json.dumps({"page_errors":errors,"mobile_overflow":overflow,"scan_text":await page.locator("#status-description").inner_text()},ensure_ascii=True))
        await browser.close()
asyncio.run(main())
