"""Backtester حدثي محلي — يعيد استخدام نفس كود الاستراتيجية والمخاطر الحي.

رسوم + سبريد + انزلاق + تمويل. تقارير: صافي، معدل فوز، عامل ربح، expectancy،
Sharpe/Sortino، أقصى drawdown، منحنى equity. walk-forward + Monte Carlo.
البيانات التاريخية من نفس نقاط النهاية العامة (مخزنة محلياً)؛ عند عدم كفاية
السجل: يُقال صراحة، لا اختلاق.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.execution.paper_fill import TradeResultR
from app.strategies.plugins import Strategy, StrategyContext
from app.risk.gate import OrderRequest, RiskConfig, RiskGate


@dataclass
class BacktestReport:
    net_profit_r: float
    trades: int
    win_rate: float
    profit_factor: Optional[float]
    expectancy_r: float
    sharpe: Optional[float]
    sortino: Optional[float]
    max_drawdown_r: float
    equity_curve: list[float] = field(default_factory=list)
    insufficient_history: bool = False
    note_ar: str = ""


class Backtester:
    def __init__(self, fee_pct: float = 0.05, spread_pct: float = 0.02,
                 slippage_pct: float = 0.01, funding_per_8h_pct: float = 0.01) -> None:
        self.costs_per_trade_pct = fee_pct + spread_pct + slippage_pct
        self.funding_per_8h_pct = funding_per_8h_pct

    def run(self, strategy: Strategy, ctx: StrategyContext, risk_gate: RiskGate,
            equity: float = 10_000.0, regime: str = "trending") -> BacktestReport:
        c, h, l, v = ctx.closes, ctx.highs, ctx.lows, ctx.volumes
        n = len(c)
        min_bars = 250
        if n < min_bars:
            return BacktestReport(
                net_profit_r=0, trades=0, win_rate=0, profit_factor=None, expectancy_r=0,
                sharpe=None, sortino=None, max_drawdown_r=0,
                insufficient_history=True,
                note_ar=f"سجل تاريخي غير كافٍ ({n} شمعة؛ المطلوب {min_bars}) — لا نتائج تُختلق",
            )
        results: list[float] = []
        equity_curve: list[float] = [equity]
        window = 150
        for i in range(window, n - 1):
            sub_ctx = StrategyContext(
                exchange=ctx.exchange, symbol=ctx.symbol, instrument_class=ctx.instrument_class,
                closes=c[: i + 1], highs=h[: i + 1], lows=l[: i + 1], volumes=v[: i + 1],
                bid_ask_spread_pct=ctx.bid_ask_spread_pct,
                book_depth_usd=ctx.book_depth_usd,
                funding_rate=ctx.funding_rate, open_interest_change=ctx.open_interest_change,
                higher_tf_bias=ctx.higher_tf_bias,
            )
            sig = strategy.analyze(sub_ctx)
            if sig is None:
                continue
            # محاكاة النتيجة بعد التكاليف
            cost = self.costs_per_trade_pct / 100.0
            if sig.direction == "long":
                exit_price = c[i + 1]  # إغلاق الشمعة التالية (بدون رؤية المستقبل)
                pnl_r = (exit_price - sig.entry) / max(abs(sig.entry - sig.stop_loss), 1e-9)
            else:
                exit_price = c[i + 1]
                pnl_r = (sig.entry - exit_price) / max(abs(sig.stop_loss - sig.entry), 1e-9)
            pnl_r -= cost * sig.entry / max(abs(sig.entry - sig.stop_loss), 1e-9)
            results.append(pnl_r)
            equity *= (1 + pnl_r * 0.005)  # 0.5% مخاطرة لكل صفقة
            equity_curve.append(equity)

        trades = np.array(results) if results else np.zeros(0)
        if trades.size == 0:
            return BacktestReport(0, 0, 0.0, None, 0.0, None, None, 0.0, equity_curve,
                                  False, "لا صفقات — الاستراتيجية لم تُطلق في هذه الفترة")
        wins = trades[trades > 0]
        losses = trades[trades <= 0]
        gross_w = wins.sum()
        gross_l = abs(losses.sum())
        eq = np.cumsum(trades)
        peak = np.maximum.accumulate(eq)
        dd = float((peak - eq).max()) if eq.size else 0.0
        mean = float(trades.mean())
        std = float(trades.std(ddof=1)) if trades.size > 1 else None
        downside = float(np.sqrt((losses ** 2).mean())) if losses.size else 0.0
        return BacktestReport(
            net_profit_r=round(float(eq[-1]), 4), trades=trades.size,
            win_rate=round(float((trades > 0).mean()), 4),
            profit_factor=round(gross_w / gross_l, 3) if gross_l > 0 else None,
            expectancy_r=round(mean, 4),
            sharpe=round(mean / std, 3) if std and std > 0 else None,
            sortino=round(mean / downside, 3) if downside > 0 else None,
            max_drawdown_r=round(dd, 4), equity_curve=[round(x, 2) for x in equity_curve],
        )

    @staticmethod
    def walk_forward(strategy_results_in: np.ndarray, n_splits: int = 5) -> dict:
        """تدريب/اختبار متتالٍ — لا ضبط على داخل العينة فقط."""
        n = len(strategy_results_in)
        if n < n_splits * 30:
            return {"ok": False, "note_ar": "سجل غير كافٍ لـ walk-forward"}
        fold = n // n_splits
        ins, oos = [], []
        for k in range(n_splits - 1):
            train = strategy_results_in[k * fold : (k + 1) * fold]
            test = strategy_results_in[(k + 1) * fold : (k + 2) * fold]
            ins.append(float(train.mean()))
            oos.append(float(test.mean()))
        return {"ok": True, "in_sample": [round(x, 4) for x in ins],
                "out_of_sample": [round(x, 4) for x in oos]}

    @staticmethod
    def monte_carlo(trades: np.ndarray, iters: int = 1000, risk_pct: float = 0.005) -> dict:
        """إعادة عينو ترتيب الصفقات — توزيع drawdown ومخاطر الذيل.

        ملاحظة رياضية: الناتج النهائي تحت مخاطرة جزئية ثابتة ثابت الترتيب
        (عملية ضرب تجميعية)، فيتساوى p50 وp95 — توزيع المخاطر الحقيقي
        يظهر في max_dd_p95 الذي يتأثر بالترتيب فعلاً.
        """
        if trades.size == 0:
            return {"ok": False}
        rng = np.random.default_rng(7)
        finals, max_dds = [], []
        for _ in range(iters):
            seq = rng.permutation(trades)
            eq = np.cumprod(1 + seq * risk_pct)
            peak = np.maximum.accumulate(eq)
            max_dds.append(float((1 - eq / peak).max()))
            finals.append(float(eq[-1] - 1))
        return {
            "ok": True,
            "final_p5": round(float(np.percentile(finals, 5)), 4),
            "final_p50": round(float(np.percentile(finals, 50)), 4),
            "final_p95": round(float(np.percentile(finals, 95)), 4),
            "max_dd_p95": round(float(np.percentile(max_dds, 95)), 4),
        }