"""M2 — ذكاء الحيتان.

قواعد صارمة:
- التداولات المنفَّذة فقط تُحلَّل (aggressor + delta + امتصاص).
- أحداث الدفتر (أوامر كبيرة تظهر/تُسحب/تُعاد تعبئتها) = "حدث دفتر، نية غير مؤكدة".
- الأمر غير المُنفَّذ ليس تداولاً أبداً ولا يوصف كذلك.
- Spoofing يوسم "ممكن" فقط، لا يقيناً.
- لا نسبة محافظ/تداولات لأشخاص أو كيانات معرفة أبداً.
- On-chain = غير متوفر افتراضياً مع السبب (لا مصدر مجاني مسموح).
"""
from __future__ import annotations

from datetime import datetime

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.models import BookSnapshot, Trade, utcnow


@dataclass
class WhaleTradeEvent:
    trade: Trade
    notional_usd: float
    percentile: float
    label: str = "large_trade"


@dataclass
class BookIntentEvent:
    exchange: str
    symbol: str
    side: str                     # bid | ask
    price: float
    size_delta: float
    action: str                   # appeared | refilled | pulled
    ts: datetime = field(default_factory=utcnow)
    label: str = "order_book_event_intent_unconfirmed"


@dataclass
class AccumulationSignal:
    symbol: str
    exchange: str
    direction: str                # accumulation | distribution
    cum_delta: float
    evidence_ar: str
    confidence: float
    ts: datetime = field(default_factory=utcnow)


class WhaleWatcher:
    def __init__(self, min_notional_usd: float = 50_000.0, percentile_window: int = 500,
                 percentile_threshold: float = 99.0) -> None:
        self.min_notional_usd = min_notional_usd
        self.percentile_window = percentile_window
        self.percentile_threshold = percentile_threshold

    def detect_large_trades(self, trades: list[Trade]) -> list[WhaleTradeEvent]:
        if not trades:
            return []
        sizes = np.array([t.price * t.size for t in trades])
        window = sizes[-self.percentile_window:]
        threshold_value = float(np.percentile(window, self.percentile_threshold)) if len(window) >= 50 else float("inf")
        events = []
        for t, notional in zip(trades, sizes):
            pct = float((window < notional).mean() * 100) if len(window) else 0.0
            if notional >= self.min_notional_usd and notional >= threshold_value:
                events.append(WhaleTradeEvent(trade=t, notional_usd=float(notional), percentile=pct))
        return events

    def book_events(self, prev: BookSnapshot, curr: BookSnapshot, big_size: float) -> list[BookIntentEvent]:
        """تغيرات أوامر كبيرة — نية غير مؤكدة، ليست تداولات منفذة."""
        events: list[BookIntentEvent] = []
        for side, prev_lvls, curr_lvls in (("bid", prev.bids, curr.bids), ("ask", prev.asks, curr.asks)):
            prev_map = {lvl.price: lvl.size for lvl in prev_lvls}
            curr_map = {lvl.price: lvl.size for lvl in curr_lvls}
            for price, size in curr_map.items():
                old = prev_map.get(price, 0.0)
                delta = size - old
                if abs(delta) >= big_size:
                    if delta < 0:
                        action = "pulled"
                    elif old < big_size:
                        action = "appeared"      # أمر كبير لم يكن موجوداً قبل لحظة
                    else:
                        action = "refilled"      # كان كبيراً أصلاً وأُعيدت تعبئته
                    events.append(BookIntentEvent(
                        exchange=curr.exchange, symbol=curr.symbol, side=side,
                        price=price, size_delta=delta, action=action,
                    ))
            for price, size in prev_map.items():
                if price not in curr_map and size >= big_size:
                    events.append(BookIntentEvent(
                        exchange=curr.exchange, symbol=curr.symbol, side=side,
                        price=price, size_delta=-size, action="pulled",
                    ))
        return events

    def detect_spoof_possible(self, events: list[BookIntentEvent], fills_at_prices: set[float]) -> list[str]:
        """أنماط spoofing محتملة — يوسم 'ممكن' فقط: أوامر كبيرة وُضعت وأُلغيت بلا أي تنفيذ."""
        flags: list[str] = []
        for e in events:
            if e.action in ("appeared", "pulled") and e.price not in fills_at_prices and abs(e.size_delta) > 0:
                flags.append(
                    f"نمط spoofing ممكن (غير مؤكد): أمر كبير {e.action} عند {e.price} "
                    f"دون تنفيذ ملحوظ — حدث دفتر، نية غير مؤكدة"
                )
        return flags

    def accumulation(self, trades: list[Trade]) -> Optional[AccumulationSignal]:
        """تراكم/توزيع من التدفق المنفذ + انحراف الدلتا."""
        if len(trades) < 30:
            return None
        delta = sum(t.size if t.aggressor == "buy" else -t.size for t in trades)
        vol = sum(t.size for t in trades) or 1.0
        ratio = delta / vol
        if abs(ratio) < 0.3:
            return None
        direction = "accumulation" if ratio > 0 else "distribution"
        return AccumulationSignal(
            symbol=trades[0].symbol, exchange=trades[0].exchange, direction=direction,
            cum_delta=delta,
            evidence_ar=(
                f"دلتا التدفق المنفذ {delta:.4f} ({ratio:.0%} من الحجم) "
                f"مع اتجاه {('شراء عدواني' if ratio > 0 else 'بيع عدواني')} — نمط "
                f"{'تراكم' if ratio > 0 else 'توزيع'} محتمل من التداولات المنفذة"
            ),
            confidence=min(80.0, 40 + abs(ratio) * 50),
        )

    @staticmethod
    def onchain_status() -> str:
        return "غير متوفر: لا يوجد مصدر on-chain رسمي مجاني يسمح الاستخدام بلا مفاتيح حالياً (ASSUMPTIONS A2)"