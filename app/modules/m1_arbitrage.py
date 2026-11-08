"""M1 — ماسح التحكيم عبر المنصات.

قواعد صارمة:
- أدوات متطابقة فقط (نفس الأصل + الفئة + عملة التسعير).
- سير الدفترين فعلياً للعمق المطلوب — ليس سبريد اسمي.
- تصنيف كل فرصة: معلوماتية (informational) أم قابلة للتنفيذ (executable) مع السبب.
- الفرصة التي تحتاج تحويل أصول بين منصات = معلوماتية إلا بتمكين صريح للزوج.
- لا تنفيذ تلقائي إلا بتمكين صريح لكل زوج منصات من المستخدم.
- السبريد النظري ليس ربحاً محققاً أبداً.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models import BookSnapshot, utcnow


@dataclass
class WalkResult:
    avg_price: float
    filled_qty: float
    deepest_price: float
    sufficient_depth: bool


def walk_book(levels: list, qty: float) -> WalkResult:
    """سير دفتر حقيقي لكمية: متوسط سعر التنفيذ الفعلي، وليس أفضل سعر اسمي."""
    remaining = qty
    cost = 0.0
    deepest = 0.0
    for lvl in levels:
        take = min(remaining, lvl.size)
        cost += take * lvl.price
        deepest = lvl.price
        remaining -= take
        if remaining <= 0:
            break
    filled = qty - max(remaining, 0)
    if filled <= 0:
        return WalkResult(0.0, 0.0, 0.0, False)
    return WalkResult(cost / filled, filled, deepest, remaining <= 0)


@dataclass
class ArbOpportunity:
    base: str
    quote: str
    instrument_class: str
    buy_exchange: str
    sell_exchange: str
    qty: float
    gross_spread_pct: float
    net_spread_pct: float
    classification: str                 # informational | executable
    reason_ar: str
    components: dict = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)


class ArbitrageScanner:
    def __init__(self, taker_fees: dict[str, float], freshness_ms: int = 3000,
                 latency_ms: float = 150.0, auto_execute_pairs: Optional[set] = None) -> None:
        self.taker_fees = taker_fees      # لكل منصة، من جدول منشور أو إعداد المستخدم
        self.freshness_ms = freshness_ms
        self.latency_ms = latency_ms      # يقاس فعلياً في الإنتاج؛ قيمة افتراضية متحفظة
        self.auto_execute_pairs = auto_execute_pairs or set()

    def compare(
        self,
        base: str, quote: str, instrument_class: str, qty: float,
        books: dict[str, BookSnapshot],
        recent_volatility: float,  # تقلب قصير المدى كنسبة عشرية لكل ثانية
    ) -> Optional[ArbOpportunity]:
        # أدوات متطابقة فقط — لا خلط فئات (نفس الأصل/الفئة/التسعير مفروض من المستدعي)
        now = utcnow()
        fresh_books: dict[str, BookSnapshot] = {}
        for ex, b in books.items():
            age_ms = (now - b.ts).total_seconds() * 1000
            if age_ms > self.freshness_ms:
                continue  # دفتر قديم — يُستبعد بسببه
            fresh_books[ex] = b
        if len(fresh_books) < 2:
            return None

        best: tuple[float, ArbOpportunity] | None = None
        exs = list(fresh_books)
        for i in range(len(exs)):
            for j in range(len(exs)):
                if i == j:
                    continue
                buy_ex, sell_ex = exs[i], exs[j]
                buy_walk = walk_book(fresh_books[buy_ex].asks, qty)
                sell_walk = walk_book(fresh_books[sell_ex].bids, qty)
                if not buy_walk.sufficient_depth or not sell_walk.sufficient_depth:
                    continue
                gross = (sell_walk.avg_price - buy_walk.avg_price) / buy_walk.avg_price
                latency_decay = recent_volatility * (self.latency_ms / 1000.0)
                fee_buy = self.taker_fees.get(buy_ex)
                fee_sell = self.taker_fees.get(sell_ex)
                if fee_buy is None or fee_sell is None:
                    # رسوم غير معروفة لمنصة واحدة على الأقل — معلوماتية دائماً بسبب واضح
                    classification = "informational"
                    reason = "رسوم taker غير معروفة لمنصة واحدة على الأقل — لا يمكن التأكد من قابلية التنفيذ"
                    net = None
                else:
                    fees = fee_buy + fee_sell
                    net = gross - fees - latency_decay
                    pair_enabled = (buy_ex, sell_ex) in self.auto_execute_pairs
                    if net > 0:
                        # عبر منصات يتطلب تواجداً مسبقاً أو تحويل أصول (بطيء) — معلوماتي إلا بتمكين صريح
                        classification = "executable" if pair_enabled else "informational"
                        reason = (
                            f"سبريد صافٍ موجب {net:.4%} بعد الرسوم ({fees:.4%}) وتخفيض الكمون "
                            f"({latency_decay:.4%})، لكن يتطلب تواجداً مسبقاً على الجانبين أو تحويل أصول "
                            f"بين المنصات — التنفيذ الآلي {'مُمكّن' if pair_enabled else 'غير مُمكّن'}"
                        )
                    else:
                        classification = "informational"
                        reason = f"السبريد الصافي سالب/صفر ({net:.4%}) بعد الرسوم والكمون"
                gross_pct = gross * 100
                if best is None or gross_pct > best[0]:
                    best = (gross_pct, ArbOpportunity(
                        base=base, quote=quote, instrument_class=instrument_class,
                        buy_exchange=buy_ex, sell_exchange=sell_ex, qty=qty,
                        gross_spread_pct=round(gross * 100, 4),
                        net_spread_pct=round((net if net is not None else gross) * 100, 4),
                        classification=classification, reason_ar=reason,
                        components={
                            "buy_avg_price": buy_walk.avg_price, "sell_avg_price": sell_walk.avg_price,
                            "fees_known": fee_buy is not None and fee_sell is not None,
                            "latency_decay_pct": latency_decay * 100,
                            "fresh_books": list(fresh_books.keys()),
                        },
                    ))
        return best[1] if best else None