"""M3 — ماسح الإدراجات الجديدة والاختراقات.

قواعد:
- كشف إدراجات جديدة من فروق استكشاف السوق.
- الاختراق = إغلاق خارج النطاق + زيادة حجم (+ إعادة اختبار اختيارية). الذيل وحده ليس اختراقاً.
- Pump مشبوه: صعود سريع + عمق رقيق + سبريد واسع + حركة بمنصة واحدة بلا تأكيد — وسم خطر وحظر تداول آلي.
"""
from __future__ import annotations

from datetime import datetime

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.engine import indicators as ind
from app.models import InstrumentSpec, utcnow


@dataclass
class BreakoutEvent:
    exchange: str
    symbol: str
    direction: str
    range_high: float
    range_low: float
    close: float
    volume_ratio: float
    retest_held: Optional[bool]
    risk_label: str = "normal"          # normal | suspicious
    evidence_ar: list[str] = field(default_factory=list)
    ts: datetime = field(default_factory=utcnow)


@dataclass
class NewListingEvent:
    spec: InstrumentSpec
    note_ar: str
    ts: datetime = field(default_factory=utcnow)


class ListingBreakoutScanner:
    def __init__(self, auto_trade_blocklist: Optional[set] = None) -> None:
        self.known_symbols: set[tuple[str, str]] = set()
        self.blocklist = auto_trade_blocklist if auto_trade_blocklist is not None else set()

    def diff_new_listings(self, current: list[InstrumentSpec]) -> list[NewListingEvent]:
        events = []
        for s in current:
            key = (s.exchange, s.symbol)
            if key not in self.known_symbols:
                events.append(NewListingEvent(
                    spec=s,
                    note_ar=f"إدراج جديد مكتشف على {s.exchange}: {s.symbol} (فئة {s.instrument_class.value}) — بيانات أولية قليلة، حذر من السبريد والعمق",
                ))
                if s.status != "TRADING":
                    self.blocklist.add(key)
        self.known_symbols = {(s.exchange, s.symbol) for s in current}
        return events

    def breakout(
        self, exchange: str, symbol: str,
        closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, volumes: np.ndarray,
        depth_usd: float, spread_pct: float,
        cross_exchange_moves: Optional[dict[str, float]] = None,
    ) -> Optional[BreakoutEvent]:
        if len(closes) < 60:
            return None
        # انضغاط تقلب: Bollinger bandwidth وATR عند مئويات دنيا
        _, upper, lower = ind.bollinger(closes)
        if math.isnan(upper[-1]):
            return None
        bw = (upper[-20:] - lower[-20:]) / closes[-20:]
        atr_vals = ind.atr(highs, lows, closes)
        atr_pct = atr_vals[-20:] / closes[-20:]
        compressed = np.nanpercentile(bw, 20) > np.nanmean(bw) * 0.9 if not np.all(np.isnan(bw)) else False
        # نطاق ما قبل الاختراق
        rng_high = float(np.max(highs[-40:-1]))
        rng_low = float(np.min(lows[-40:-1]))
        close = float(closes[-1])
        vol_ratio = float(volumes[-1] / (np.mean(volumes[-20:]) + 1e-12))
        broke_up = close > rng_high and closes[-2] <= rng_high
        broke_down = close < rng_low and closes[-2] >= rng_low
        if not (broke_up or broke_down) or vol_ratio < 1.5:
            return None  # الذيل وحده ليس اختراقاً: نريد إغلاقاً خارج النطاق + حجم
        # retest اختياري: شمعة سابقة عادت للنطاق ثم أغلقت خارجه
        retest = bool(np.any(np.abs(closes[-3:-1] - rng_high) / rng_high < 0.002)) if broke_up else \
                 bool(np.any(np.abs(closes[-3:-1] - rng_low) / rng_low < 0.002))
        direction = "up" if broke_up else "down"
        risk = "normal"
        evidence = [
            f"إغلاق {'فوق' if broke_up else 'تحت'} نطاق [{rng_low:.4f}, {rng_high:.4f}] عند {close:.4f}",
            f"حجم الاختراق {vol_ratio:.1f}× متوسط 20 شمعة",
        ]
        # حركة بمنصة واحدة بلا تأكيد عبر منصات + عمق رقيق + سبريد واسع = pump مشبوه
        others = cross_exchange_moves or {}
        confirmed_elsewhere = any(
            abs(chg) > 0 and (chg > 0) == (direction == "up")
            for chg in others.values()
        )
        if (not confirmed_elsewhere and others) or depth_usd < 20_000 or spread_pct > 0.15:
            risk = "suspicious"
            if not confirmed_elsewhere and others:
                evidence.append("الحركة بمنصة واحدة دون تأكيد على منصات أخرى — نمط pump محتمل")
            if depth_usd < 20_000:
                evidence.append(f"عمق الدفتر رقيق (${depth_usd:,.0f}) — تنفيذ حقيقي صعب")
            if spread_pct > 0.15:
                evidence.append(f"سبريد واسع ({spread_pct:.2f}%) — تكلفة تنفيذ عالية")
            self.blocklist.add((exchange, symbol))
        return BreakoutEvent(
            exchange=exchange, symbol=symbol, direction=direction,
            range_high=rng_high, range_low=rng_low, close=close,
            volume_ratio=vol_ratio, retest_held=retest,
            risk_label=risk, evidence_ar=evidence,
        )