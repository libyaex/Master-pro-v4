"""بوابة المخاطر — صلبة قبل كل أمر. لا أمر يتجاوزها.

- حجم بنسبة مخاطرة مئوية بقواعد tick/lot دقيقة
- SL/TP إلزاميان، حد أدنى عائد/مخاطرة
- حدود خسارة يومية/أسبوعية + قاطع drawdown
- فحوص سيولة وسبريد (رفض إن تجاوز الأمر انزلاقاً محدداً)
- حراسة أخبار تقلبية (ATR/سبريد/معدل نقرات شاذ) توقف التداول مؤقتاً
- kill switch يلغي كل الأوامر ويغلق المراكز حتى لو كان التطبيق غير متصل (مهمة أولوية قصوى بالطابور)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.models import InstrumentSpec


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.5       # % من رصيد الحساب لكل صفقة
    min_risk_reward: float = 1.5
    max_open_trades: int = 5
    max_per_symbol_exposure_pct: float = 20.0
    max_correlated_exposure_pct: float = 40.0
    daily_loss_limit_pct: float = 3.0
    weekly_loss_limit_pct: float = 6.0
    drawdown_circuit_breaker_pct: float = 10.0
    max_slippage_pct: float = 0.10        # رفض إن حرك الأمر الدفتر أكثر من هذا
    max_spread_pct: float = 0.15
    news_guard_atr_multiplier: float = 3.0
    news_guard_tick_rate_per_sec: float = 50.0


@dataclass
class OrderRequest:
    exchange: str
    symbol: str
    side: str                        # buy | sell
    direction: str                  # long | short
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    instrument: InstrumentSpec
    account_equity: float
    open_trades: int
    symbol_exposure_pct: float
    correlated_exposure_pct: float
    daily_pnl_pct: float
    weekly_pnl_pct: float
    current_drawdown_pct: float
    book_slippage_pct: float         # انزلاق محسوب من الدفتر الفعلي
    current_spread_pct: float
    atr_now: float
    atr_baseline: float
    tick_rate_per_sec: float = 0.0


@dataclass
class RiskDecision:
    approved: bool
    reason_ar: str
    position_size: float = 0.0


def _risk_amount_to_size(risk_amount: float, entry: float, stop: float, spec: InstrumentSpec) -> float:
    """حجم دقيق بقواعد lot step/tick — لا تقريب عشوائي."""
    per_unit = abs(entry - stop)
    if per_unit <= 0:
        return 0.0
    raw = risk_amount / per_unit
    step = spec.lot_step
    # epsilon: يمنع بتر الأحجام الدقيقة بسبب خطأ الفاصلة العائمة (مثل 19.9999999 بدل 20)
    size = math.floor(raw / step + 1e-9) * step
    # فحص الحد الأدنى notional
    if spec.min_notional and size * entry < spec.min_notional:
        return 0.0
    return round(size, 10)


class RiskGate:
    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or RiskConfig()
        self.kill_switch_active = False

    def trigger_kill_switch(self) -> None:
        self.kill_switch_active = True

    def check(self, req: OrderRequest) -> RiskDecision:
        c = self.config
        if self.kill_switch_active:
            return RiskDecision(False, "kill switch نشط — كل الأوامر مرفوضة حتى إعادة التفعيل اليدوي")
        if req.stop_loss <= 0 or req.take_profit <= 0:
            return RiskDecision(False, "وقف خسارة وجني ربح إلزاميان — طلب ناقص مرفوض")
        if req.risk_reward < c.min_risk_reward:
            return RiskDecision(False, f"عائد/مخاطرة {req.risk_reward:.2f} أقل من الحد الأدنى {c.min_risk_reward}")
        if req.open_trades >= c.max_open_trades:
            return RiskDecision(False, f"عدد الصفقات المفتوحة بلغ الحد ({c.max_open_trades})")
        if req.daily_pnl_pct <= -c.daily_loss_limit_pct:
            return RiskDecision(False, f"حد الخسارة اليومية ({c.daily_loss_limit_pct}%) مستنفد — توقف اليوم")
        if req.weekly_pnl_pct <= -c.weekly_loss_limit_pct:
            return RiskDecision(False, f"حد الخسارة الأسبوعية ({c.weekly_loss_limit_pct}%) مستنفد")
        if req.current_drawdown_pct >= c.drawdown_circuit_breaker_pct:
            return RiskDecision(False, f"قاطع drawdown ({c.drawdown_circuit_breaker_pct}%) مفعّل")
        if req.symbol_exposure_pct >= c.max_per_symbol_exposure_pct:
            return RiskDecision(False, f"تعرض الرمز ({req.symbol_exposure_pct:.1f}%) فوق الحد")
        if req.correlated_exposure_pct >= c.max_correlated_exposure_pct:
            return RiskDecision(False, f"تعرض المرتبطة ({req.correlated_exposure_pct:.1f}%) فوق الحد")
        if req.book_slippage_pct > c.max_slippage_pct:
            return RiskDecision(False, f"الأمر يحرك الدفتر {req.book_slippage_pct:.3f}% فوق حد الانزلاق {c.max_slippage_pct}%")
        if req.current_spread_pct > c.max_spread_pct:
            return RiskDecision(False, f"السبريد {req.current_spread_pct:.3f}% فوق الحد {c.max_spread_pct}%")
        # حراسة تقلب الأخبار: ATR أو معدل نقرات شاذ = توقف مؤقت (بلا تقويم مدفوع)
        if req.atr_baseline > 0 and req.atr_now > c.news_guard_atr_multiplier * req.atr_baseline:
            return RiskDecision(False, f"حراسة تقلب: ATR الحالي {req.atr_now:.6f} = {req.atr_now / req.atr_baseline:.1f}× الأساس — توقف مؤقت")
        if req.tick_rate_per_sec > c.news_guard_tick_rate_per_sec:
            return RiskDecision(False, "حراسة تقلب: معدل نقرات شاذ — توقف مؤقت")

        risk_amount = req.account_equity * c.risk_per_trade_pct / 100.0
        size = _risk_amount_to_size(risk_amount, req.entry, req.stop_loss, req.instrument)
        if size <= 0:
            return RiskDecision(False, "الحجم المحسوب تحت الحد الأدنى للرمز (min notional/lot) — مرفوض")
        return RiskDecision(True, f"موافق — حجم {size} (مخاطرة {c.risk_per_trade_pct}% من {req.account_equity:.2f})", position_size=size)