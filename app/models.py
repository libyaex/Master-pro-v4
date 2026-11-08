"""نماذج أساسية مشتركة — أدوات السوق، الفئات الأربع، جودة البيانات."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InstrumentClass(str, Enum):
    CRYPTO = "crypto"
    TOKENIZED_GOLD = "tokenized_gold"      # PAXG / XAUT
    GOLD_DERIVATIVE = "gold_derivative"   # مشتقات ذهب مدرجة على منصة
    SPOT_XAUUSD = "spot_xauusd"           # ذهب فوري — يظهر غير متوفر إن لم يوجد مصدر


class InstrumentSpec(BaseModel):
    exchange: str
    symbol: str                            # الرمز الموحد مثل BTCUSDT
    base: str
    quote: str
    instrument_class: InstrumentClass
    status: str                           # "TRADING" | غير ذلك
    market_type: str                      # spot | perpetual | dated_future
    tick_size: float
    lot_step: float
    min_notional: float = 0.0
    contract_size: float = 1.0
    listing_time: Optional[datetime] = None
    raw_symbol: str = ""


class Candle(BaseModel):
    exchange: str
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = 0.0
    closed: bool = True
    data_quality: list[str] = Field(default_factory=list)  # أعلام مثل duplicate/missing/stale


class Trade(BaseModel):
    exchange: str
    symbol: str
    trade_id: str
    ts: datetime
    price: float
    size: float
    aggressor: str                       # buy | sell | unknown


class BookLevel(BaseModel):
    price: float
    size: float


class BookSnapshot(BaseModel):
    exchange: str
    symbol: str
    ts: datetime
    bids: list[BookLevel]
    asks: list[BookLevel]
    sequence: Optional[int] = None
    fresh_ms: int = 0                    # عمر اللقطة بالملي ثانية عند الاستخدام


class FundingInfo(BaseModel):
    exchange: str
    symbol: str
    ts: datetime
    rate: float
    next_funding_ts: Optional[datetime] = None


class OpenInterest(BaseModel):
    exchange: str
    symbol: str
    ts: datetime
    oi: float
    oi_quote: float = 0.0


class SourceHealth(BaseModel):
    source: str
    available: bool
    reason: Optional[str] = None         # مطلوب عند unavailable
    last_good_ts: Optional[datetime] = None
    rate_limit_used: Optional[float] = None


class Unavailable:
    """قيمة صريحة لمصدر غير متوفر — لا تقدير صامت أبداً."""
    def __init__(self, reason: str):
        self.reason = reason