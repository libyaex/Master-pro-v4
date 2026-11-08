# M6 — ترتيب الفرص بدرجة شفافة وقابلة للضبط.
# - كل عنصر يعرض مكونات درجته وسبب ترتيبه.
# - بيانات حرجة ناقصة = ترتيب أدنى أو استبعاد + وسم غير متوفر.
# - الترتيب معلوماتي ولا يتجاوز طبقة المخاطر أبداً.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models import utcnow

DEFAULT_WEIGHTS = {
    "liquidity": 0.25,
    "spread": 0.15,
    "regime_fit": 0.15,
    "volatility_fit": 0.10,
    "strategy_agreement": 0.20,
    "execution_feasibility": 0.15,
}


@dataclass
class RankedItem:
    kind: str
    label: str
    score: float
    components: dict = field(default_factory=dict)
    unavailable_reasons: list = field(default_factory=list)
    reason_ar: str = ""
    ts: datetime = field(default_factory=utcnow)


class OpportunityRanker:
    def __init__(self, weights=None) -> None:
        w = {**DEFAULT_WEIGHTS, **(weights or {})}
        total = sum(w.values())
        self.weights = {k: v / total for k, v in w.items()}

    def rank(self, items):
        # items: قواميس بمفاتيح raw_components وmetadata. الدرجات 0-1 لكل مكون.
        ranked = []
        for it in items:
            raw = it.get("raw_components", {})
            comps = {}
            missing = []
            for key, weight in self.weights.items():
                val = raw.get(key)
                if val is None or (isinstance(val, float) and val != val):
                    missing.append(key)
                    comps[key] = 0.0
                else:
                    comps[key] = float(val) * weight
            score = sum(comps.values()) * 100
            critical = {"liquidity", "execution_feasibility"}
            excluded = bool(critical & set(missing))
            if excluded:
                score *= 0.25
            ranked.append(RankedItem(
                kind=it.get("kind", "unknown"), label=it.get("label", ""),
                score=round(score, 2), components=comps,
                unavailable_reasons=["غير متوفر: " + m for m in missing],
                reason_ar=(
                    "استُبعد جزئياً لنقص بيانات حرجة (" + ", ".join(missing) + ")"
                    if excluded and missing
                    else "درجة أعلى من: " + ", ".join(
                        k + "=" + format(v, ".2f")
                        for k, v in sorted(comps.items(), key=lambda x: -x[1])[:3]
                    )
                ),
            ))
        ranked.sort(key=lambda r: -r.score)
        return ranked
