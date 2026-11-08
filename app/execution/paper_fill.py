"""نمذجة التنفيذ الورقي — تعبئة محاكاة بعمولات وسبريد وانزلاق من الدفتر الحقيقي."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.models import BookSnapshot, utcnow


@dataclass
class PaperFill:
    symbol: str
    exchange: str
    side: str
    requested_price: float
    filled_price: float
    qty: float
    fee: float
    slippage_pct: float
    ts: datetime


class PaperFillModel:
    """تعبئة واقعية: نمشي الدفتر الفعلي + عمولة taker + نتحقق من العمق."""

    def __init__(self, taker_fee_pct: float = 0.05, fee_overrides: Optional[dict[str, float]] = None) -> None:
        self.taker_fee_pct = taker_fee_pct
        self.fee_overrides = fee_overrides or {}

    def simulate_fill(self, book: BookSnapshot, side: str, qty: float, exchange: str,
                      symbol: str, reference_price: Optional[float] = None) -> Optional[PaperFill]:
        levels = book.asks if side == "buy" else book.bids
        remaining = qty
        cost = 0.0
        for lvl in levels:
            take = min(remaining, lvl.size)
            cost += take * lvl.price
            remaining -= take
            if remaining <= 1e-12:
                break
        filled = qty - max(remaining, 0.0)
        if filled <= 0:
            return None  # لا عمق كافٍ — لا تعبئة وهمية أبداً
        avg_price = cost / filled
        fee_pct = self.fee_overrides.get(exchange, self.taker_fee_pct)
        fee = cost * fee_pct / 100.0
        ref = reference_price or (levels[0].price if levels else avg_price)
        slip = abs(avg_price - ref) / ref * 100 if ref else 0.0
        return PaperFill(
            symbol=symbol, exchange=exchange, side=side,
            requested_price=ref, filled_price=avg_price, qty=filled,
            fee=fee, slippage_pct=round(slip, 4), ts=utcnow(),
        )


@dataclass
class TradeResultR:
    """نتيجة صفقة بوحدات R (مضاعف المخاطرة) — وحدة قياس موحدة للتقييم (M5)."""
    entry: float
    exit: float
    stop: float
    direction: str

    def r_multiple(self) -> float:
        risk = abs(self.entry - self.stop)
        if risk <= 0:
            return 0.0
        move = (self.exit - self.entry) if self.direction == "long" else (self.entry - self.exit)
        return move / risk