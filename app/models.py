from dataclasses import dataclass
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict, field_validator


@dataclass
class OrderObservation:
    shop_code: str
    shopify_order_id: str
    source_item_ids: list[str]
    store_name: str
    display_number: str
    amount: Decimal
    currency: str | None = None
    purchase_date: str | None = None


@dataclass
class ScanSnapshot:
    orders: list[OrderObservation]
    pages: int = 1
    complete: bool = True


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(default="", max_length=120)
    password: str = Field(default="", max_length=500)
    bot_token: str = Field(default="", max_length=300)
    chat_id: str = Field(default="", max_length=120)
    topic_id: int | None = Field(default=None, gt=0)
    interval_seconds: int = Field(default=120, ge=30, le=86400)
    enabled: bool = False
    stores: list[str] = Field(default_factory=list, max_length=100)
    lookback_days: int | None = Field(default=30, ge=1, le=36500)

    @field_validator("username", "bot_token", "chat_id")
    @classmethod
    def trim(cls, v):
        return v.strip()

    @field_validator("stores")
    @classmethod
    def normalize_stores(cls, values):
        import re
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", s.strip()) for s in values):
            raise ValueError("Store code không hợp lệ")
        return sorted(set(s.strip() for s in values))

    @field_validator("bot_token")
    @classmethod
    def token_shape(cls, value):
        import re
        if value and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{10,}", value):
            raise ValueError("Bot token không hợp lệ")
        return value

    @field_validator("chat_id")
    @classmethod
    def chat_shape(cls, value):
        import re
        if value and not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", value):
            raise ValueError("Chat ID không hợp lệ")
        return value
