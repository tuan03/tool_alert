import httpx
import pytest
from app.telegram import TelegramClient, format_message

def test_message_does_not_invent_currency_or_sum():
    msg = format_message({"store_name":"A < B", "shop_code":"S", "display_number":"#12","amount":"27.81","currency":None})
    assert "A < B" in msg and "27.81" in msg and "#12" in msg
    assert "USD" not in msg

@pytest.mark.parametrize("code,payload,want", [
    (200, {"ok":True,"result":{"message_id":7}}, "sent"),
    (429, {"ok":False,"parameters":{"retry_after":33}}, "retryable"),
    (401, {"ok":False,"description":"token secret"}, "failed"),
    (500, {"ok":False}, "retryable")])
async def test_transport_results(code,payload,want):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(code,json=payload))) as h:
        result = await TelegramClient(h).send("123:abcdefghijk", "-1", None, "hello")
    assert result.status == want
    assert "secret" not in (result.error or "")
    if code == 200: assert result.message_id == 7
    if code == 429: assert result.retry_after == 33

async def test_ambiguous_timeout_never_marks_sent():
    def handler(r): raise httpx.ReadTimeout("token in url")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as h:
        result = await TelegramClient(h).send("123:abcdefghijk", "-1", None, "hello")
    assert result.status == "uncertain"
    assert "token" not in result.error
