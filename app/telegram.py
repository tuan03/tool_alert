from dataclasses import dataclass
import httpx


@dataclass
class DeliveryResult:
    status: str
    message_id: int | None = None
    retry_after: int | None = None
    error: str | None = None


def format_message(order: dict) -> str:
    suffix = " " + order["currency"] if order.get("currency") else ""
    return ("🛍 Đơn hàng mới\n"
            f"Store: {order['store_name']} ({order['shop_code']})\n"
            f"Đơn: {order['display_number']}\n"
            f"Giá trị đơn: {order['amount']}{suffix}")


class TelegramClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def send(self, token: str, chat_id: str, topic_id: int | None, text: str) -> DeliveryResult:
        if not token or not chat_id:
            return DeliveryResult("failed",error="Chưa cấu hình bot token hoặc chat ID.")
        body = {"chat_id":chat_id,"text":text}
        if topic_id:
            body["message_thread_id"] = topic_id
        try:
            response = await self.client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=25)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return DeliveryResult("retryable",error="Không kết nối được Telegram.")
        except httpx.HTTPError:
            return DeliveryResult("uncertain",error="Không xác định Telegram đã nhận tin hay chưa. Kiểm tra trước khi thử lại.")
        try:
            data = response.json()
        except ValueError:
            return DeliveryResult("uncertain",error="Phản hồi Telegram không hợp lệ; kiểm tra tin trước khi thử lại.")
        if response.status_code == 200 and data.get("ok") and isinstance(data.get("result",{}).get("message_id"),int):
            return DeliveryResult("sent",message_id=data["result"]["message_id"])
        code = data.get("error_code",response.status_code)
        if code == 429:
            seconds = data.get("parameters",{}).get("retry_after",60)
            return DeliveryResult("retryable",retry_after=int(seconds),error="Telegram giới hạn tốc độ.")
        if response.status_code >= 500:
            return DeliveryResult("retryable",error="Telegram tạm thời lỗi.")
        return DeliveryResult("failed",error=f"Telegram từ chối yêu cầu (mã {response.status_code}); kiểm tra bot, chat và quyền gửi.")


async def send_one(repo, telegram):
    event = repo.claim_event()
    if not event:
        return False
    result = await telegram.send(repo.settings().bot_token, event["chat_id"],
                                 event["topic_id"], format_message(event["payload"]))
    repo.finish_event(event,result)
    return True
