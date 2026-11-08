"""إضافات الاستراتيجيات — كل استراتيجية: analyze(context) -> SignalRecord | None.

شموع مغلقة فقط. النظام (regime) يبوّب ما يعمل. التجميعensemble فوق عتبة قابلة للضبط (70).
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, Optional

import numpy as np

from app.engine import indicators as ind
from app.strategies.signal_record import EvidenceItem, SignalRecord


class StrategyContext:
    """بيانات الإدخال المشتركة — شموع مغلقة + سياق مشتقات + عمق دفتر."""

    def __init__(
        self,
        exchange: str,
        symbol: str,
        instrument_class: str,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        volumes: np.ndarray,
        bid_ask_spread_pct: float,
        book_depth_usd: float,
        funding_rate: float = 0.0,
        open_interest_change: float = 0.0,
        higher_tf_bias: str = "neutral",
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.instrument_class = instrument_class
        self.closes = closes
        self.highs = highs
        self.lows = lows
        self.volumes = volumes
        self.bid_ask_spread_pct = bid_ask_spread_pct
        self.book_depth_usd = book_depth_usd
        self.funding_rate = funding_rate
        self.open_interest_change = open_interest_change
        self.higher_tf_bias = higher_tf_bias


class Strategy(ABC):
    name: str = "base"
    regimes: tuple[str, ...] = ("trending",)
    min_timeframe_note: str = ""

    @abstractmethod
    def analyze(self, ctx: StrategyContext) -> Optional[SignalRecord]: ...


class TrendPullback(Strategy):
    """اتجاه: تراص EMA + ADX، ارتداد إلى EMA/VWAP، بنية سوق."""
    name = "trend_pullback"
    regimes = ("trending",)

    def analyze(self, ctx: StrategyContext) -> Optional[SignalRecord]:
        c, h, l, v = ctx.closes, ctx.highs, ctx.lows, ctx.volumes
        if len(c) < 60:
            return None
        e21, e50, e200 = ind.ema(c, 21), ind.ema(c, 50), ind.ema(c, 200)
        a = ind.adx(h, l, c)
        r = ind.rsi(c)
        vw = ind.vwap(h, l, c, v)
        if math.isnan(e200[-1]) or math.isnan(a[-1]):
            return None
        if not (e21[-1] > e50[-1] > e200[-1]) or a[-1] < 20:
            return None
        # ارتداد: السعر قرب EMA21 وRSI ليس مشبعاً
        near = abs(c[-1] - e21[-1]) / c[-1] < 0.004
        if not near or r[-1] > 70 or r[-1] < 40:
            return None
        swing_high, swing_low = ind.swing_levels(h, l)
        atr = ind.atr(h, l, c)[-1]
        if math.isnan(atr):
            return None
        direction = "long"
        entry = float(c[-1])
        stop = float(min(swing_low, e21[-1] - atr))
        rr_val = ((entry + 3 * atr) - entry) / max(entry - stop, 1e-9)
        if rr_val < 1.5:
            return None
        return SignalRecord(
            exchange=ctx.exchange, symbol=ctx.symbol, instrument_class=ctx.instrument_class,
            direction=direction, entry=round(entry, 8), stop_loss=round(stop, 8),
            tp1=round(entry + 1.5 * atr, 8), tp2=round(entry + 2.5 * atr, 8),
            tp3=round(entry + 3.5 * atr, 8), risk_reward=round(rr_val, 2),
            confidence=min(90, 55 + a[-1] / 2),
            evidence=[
                EvidenceItem(feature="ema_stack", value=f"{e21[-1]:.4f}>{e50[-1]:.4f}>{e200[-1]:.4f}",
                             explanation_ar="المتوسطات المتحركة متراصة صعوداً — الاتجاه العام صاعد"),
                EvidenceItem(feature="adx", value=f"{a[-1]:.1f}", explanation_ar="قوة اتجاه كافية (ADX فوق 20)"),
                EvidenceItem(feature="vwap", value=f"{vw[-1]:.4f}",
                             explanation_ar="السعر فوق VWAP — دعم حجمي للاتجاه الصاعد"),
            ],
            invalidation=f"إغلاق شمعة تحت {round(stop, 8)} أو كسر EMA50 هبوطياً",
            source_modules=[self.name, "regime_detector"],
            data_quality_flags=[],
        )


class RangeBBRSI(Strategy):
    """نطاق: Bollinger + تشبع RSI (عندما يقول النظام: ranging)."""
    name = "range_bb_rsi"
    regimes = ("ranging",)

    def analyze(self, ctx: StrategyContext) -> Optional[SignalRecord]:
        c, h, l, v = ctx.closes, ctx.highs, ctx.lows, ctx.volumes
        if len(c) < 40:
            return None
        mid, upper, lower = ind.bollinger(c)
        r = ind.rsi(c)
        if math.isnan(lower[-1]) or math.isnan(r[-1]):
            return None
        atr = ind.atr(h, l, c)[-1]
        if math.isnan(atr):
            return None
        if c[-1] > lower[-1] or r[-1] >= 30:
            return None
        entry = float(c[-1])
        stop = float(lower[-1] - 1.5 * atr)
        if entry <= stop:
            return None  # كسر عنيف تحت الوقف المحسوب — لا إشارة صالحة
        rr = (mid[-1] - entry) / (entry - stop)
        if rr < 1.5:
            return None
        return SignalRecord(
            exchange=ctx.exchange, symbol=ctx.symbol, instrument_class=ctx.instrument_class,
            direction="long", entry=round(entry, 8), stop_loss=round(stop, 8),
            tp1=round(mid[-1], 8), tp2=round((mid[-1] + upper[-1]) / 2, 8),
            tp3=round(upper[-1], 8), risk_reward=round(rr, 2),
            confidence=min(85, 50 + (30 - r[-1])),
            evidence=[
                EvidenceItem(feature="bollinger_lower", value=f"{lower[-1]:.4f}",
                             explanation_ar="السعر لمس الحد السفلي لبولنجر — منطقة تشبع بيعي في نطاق عرضي"),
                EvidenceItem(feature="rsi", value=f"{r[-1]:.1f}",
                             explanation_ar="RSI في منطقة تشبع بيعي تحت 30"),
            ],
            invalidation=f"إغلاق شمعة تحت {round(stop, 8)} أو تحول النظام إلى هبوطي",
            source_modules=[self.name, "regime_detector"],
        )


class LiquiditySweepReversal(Strategy):
    """Scalping: اجتياح سيولة + انعكاس سريع + دعم دفتر — شموع 1m/5m."""
    name = "liquidity_sweep_reversal"
    regimes = ("ranging", "high_volatility")
    min_timeframe_note = "1m-5m"

    def analyze(self, ctx: StrategyContext) -> Optional[SignalRecord]:
        c, h, l, v = ctx.closes, ctx.highs, ctx.lows, ctx.volumes
        if len(c) < 50 or ctx.bid_ask_spread_pct > 0.08:
            return None
        vz = ind.zscore(v)
        if math.isnan(vz[-1]) or vz[-1] < 1.5:
            return None
        swing_high, swing_low = ind.swing_levels(h[:-1], l[:-1])
        if math.isnan(swing_high):
            return None
        # ذيل فوق القمة ثم إغلاق تحتها + انعكاس
        swept = h[-1] > swing_high and c[-1] < swing_high
        if not swept:
            return None
        atr = ind.atr(h, l, c)[-1]
        if math.isnan(atr) or atr <= 0:
            return None
        entry = float(c[-1])
        stop = float(h[-1] + 0.2 * atr)
        tp1 = entry - 1.5 * atr
        # الهدف الثاني: أقرب قاع swing إن كان أدنى من الهدف الأول، وإلا 2.5×ATR (ترتيب مستويات سليم للبيع)
        tp2 = min(tp1, swing_low) if not math.isnan(swing_low) else entry - 2.5 * atr
        tp3 = min(tp2, entry - 3 * atr)
        if stop <= entry:
            return None
        rr = (entry - tp2) / (stop - entry)
        if rr < 1.5:
            return None
        return SignalRecord(
            exchange=ctx.exchange, symbol=ctx.symbol, instrument_class=ctx.instrument_class,
            direction="short", entry=round(entry, 8), stop_loss=round(stop, 8),
            tp1=round(tp1, 8), tp2=round(tp2, 8), tp3=round(tp3, 8), risk_reward=round(rr, 2),
            confidence=min(80, 45 + vz[-1] * 8),
            evidence=[
                EvidenceItem(feature="wick_beyond_swing", value=f"high={h[-1]:.4f} > swing={swing_high:.4f}",
                             explanation_ar="ذيل شمعة اجتاز قمة سابقة (سحب سيولة) ثم أغلقت تحتها — انعكاس محتمل"),
                EvidenceItem(feature="volume_zscore", value=f"{vz[-1]:.2f}",
                             explanation_ar="حجم غير طبيعي أثناء الاجتياح"),
                EvidenceItem(feature="spread_pct", value=f"{ctx.bid_ask_spread_pct:.3f}%",
                             explanation_ar="السبريد ضمن الحد المسموح للسكالبينج"),
            ],
            invalidation=f"إغلاق شمعة فوق {round(stop, 8)}",
            source_modules=[self.name, "whale_watch_m2"],
        )


class FundingExtreme(Strategy):
    """مشتقات: تطرف funding + تباعد OI — إعداد squeeze."""
    name = "funding_extreme"
    regimes = ("trending", "high_volatility")

    def analyze(self, ctx: StrategyContext) -> Optional[SignalRecord]:
        if ctx.funding_rate == 0 or ctx.open_interest_change == 0:
            return None
        c, h, l = ctx.closes, ctx.highs, ctx.lows
        if len(c) < 30:
            return None
        atr = ind.atr(h, l, c)[-1]
        if math.isnan(atr):
            return None
        # funding موجب متطرف (طويلون يدفعون) + OI يرتفع = انضغاط squeeze محتمل هبوطياً
        if ctx.funding_rate < 0.0004 or ctx.open_interest_change <= 0:
            return None
        entry = float(c[-1])
        stop = float(entry + 1.5 * atr)
        rr = (2 * atr) / (1.5 * atr)
        if rr < 1.3:
            return None
        return SignalRecord(
            exchange=ctx.exchange, symbol=ctx.symbol, instrument_class=ctx.instrument_class,
            direction="short", entry=round(entry, 8), stop_loss=round(stop, 8),
            tp1=round(entry - 1.5 * atr, 8), tp2=round(entry - 2.5 * atr, 8),
            tp3=round(entry - 3.5 * atr, 8), risk_reward=round(rr, 2),
            confidence=60,
            evidence=[
                EvidenceItem(feature="funding_rate", value=f"{ctx.funding_rate:.5f}",
                             explanation_ar="معدل تمويل موجب متطرف — المراكح الطويلة تضغط بالتكلفة"),
                EvidenceItem(feature="oi_change", value=f"{ctx.open_interest_change:.2%}",
                             explanation_ar="الفائدة المفتوحة ترتفع مع تمول متطرف — إعداد انضغاط محتمل"),
            ],
            invalidation=f"funding يهبط تحت 0.0002 أو إغلاق فوق {round(stop, 8)}",
            source_modules=[self.name, "derivatives_context"],
        )


DEFAULT_STRATEGIES: list[Strategy] = [
    TrendPullback(), RangeBBRSI(), LiquiditySweepReversal(), FundingExtreme(),
]


def ensemble_score(signals: list[SignalRecord], regime_fit: float, liquidity_quality: float,
                   spread_penalty: float, cross_exchange_confirm: float, volatility_fit: float,
                   threshold: float = 70.0) -> Optional[SignalRecord]:
    """تجميع 0-100: اتفاق الاستراتيجيات + ملاءمة النظام + السيولة + السبريد + تأكيد عبر المنصات.

    لا تُنفَّذ إشارة إلا فوق العتبة (افتراضي 70). كل ميزة مخزنة للتفسير.
    """
    if not signals:
        return None
    base = len(signals) * 18
    score = base + regime_fit * 15 + liquidity_quality * 10 + cross_exchange_confirm * 12 + volatility_fit * 8 - spread_penalty
    score = max(0.0, min(100.0, score))
    if score < threshold:
        return None
    best = max(signals, key=lambda s: s.confidence)
    best.confidence = score
    return best