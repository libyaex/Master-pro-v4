# M5 — تقييم استراتيجيات تكيفي (إحصائي، لا انفعالي).
# قواعد:
# - تقليل حجم/تعطيل استراتيجية فقط عند مبرر إحصائي: حد أدنى تداولات + فترة ثقة bootstrap على expectancy.
# - الاستراتيجية المعطلة تعود عبر فترة اختبار ورقية (probation).
# - لا ضبط معاملات على نتائج داخل العينة أبداً.
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.models import utcnow


@dataclass
class StrategyPerf:
    name: str
    trades: list = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    max_drawdown: float = 0.0
    disabled: bool = False
    probation: bool = False
    last_change_ar: str = ""


class AdaptiveEvaluator:
    def __init__(self, min_trades: int = 30, ci_confidence: float = 0.95,
                 disable_below: float = 0.0, bootstrap_iters: int = 1000) -> None:
        self.min_trades = min_trades
        self.ci_confidence = ci_confidence
        self.disable_below = disable_below
        self.bootstrap_iters = bootstrap_iters
        self.perf = {}

    def record(self, strategy, pnl_r):
        p = self.perf.setdefault(strategy, StrategyPerf(name=strategy))
        p.trades.append(pnl_r)
        if pnl_r >= 0:
            p.wins += 1
            p.gross_win += pnl_r
        else:
            p.losses += 1
            p.gross_loss += abs(pnl_r)
        equity = np.cumsum(p.trades)
        peak = np.maximum.accumulate(equity) if len(equity) else np.array([0.0])
        p.max_drawdown = float(np.max(peak - equity)) if len(equity) else 0.0

    def bootstrap_ci(self, trades):
        # فترة ثقة bootstrap على expectancy — الضجيج لا يطلق قرارات.
        rng = np.random.default_rng(42)
        n = len(trades)
        means = np.array([
            trades[rng.integers(0, n, n)].mean() for _ in range(self.bootstrap_iters)
        ])
        lo, hi = np.percentile(means, [(1 - self.ci_confidence) / 2 * 100, (1 + self.ci_confidence) / 2 * 100])
        return float(lo), float(hi)

    def evaluate(self, strategy):
        p = self.perf.get(strategy)
        if p is None or len(p.trades) < self.min_trades:
            return {
                "strategy": strategy, "status": "insufficient_data",
                "note_ar": "بيانات غير كافية للقرار الإحصائي — لا تعطيل ولا تغيير حجم",
            }
        trades = np.array(p.trades)
        expectancy = float(trades.mean())
        win_rate = p.wins / len(trades)
        profit_factor = (p.gross_win / p.gross_loss) if p.gross_loss > 0 else math.inf
        lo, hi = self.bootstrap_ci(trades)
        decision = "keep"
        note = ""
        if hi < self.disable_below:
            if not p.disabled:
                p.disabled = True
                p.probation = True
                decision = "disabled"
                note = "expectancy سالبة بثقة إحصائية (فترة ثقة علوية تحت الصفر) — تعطيل ودخول فترة اختبار ورقية"
                p.last_change_ar = utcnow().isoformat() + ": " + note
        elif p.disabled and lo > 0 and p.probation:
            p.disabled = False
            p.probation = False
            decision = "re_enabled_after_probation"
            note = "expectancy موجبة بثقة خلال فترة الاختبار الورقي — إعادة تفعيل"
            p.last_change_ar = utcnow().isoformat() + ": " + note
        return {
            "strategy": strategy, "status": decision,
            "trades": len(trades), "expectancy_r": round(expectancy, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2) if profit_factor != math.inf else None,
            "max_drawdown_r": round(p.max_drawdown, 4),
            "note_ar": note or "أداء ضمن الحدود — لا تغيير",
        }

    def audit_log(self):
        return [
            {"strategy": p.name, "disabled": p.disabled, "probation": p.probation,
             "last_change": p.last_change_ar}
            for p in self.perf.values()
        ]


def walk_forward_sanity(train_score, test_score, tolerance=0.5):
    # تأكيد خارج العينة: النتيجة الحية/الاختبارية لا يجب أن تنهار مقابل التدريب.
    if train_score <= 0:
        return False
    return test_score >= train_score * tolerance
