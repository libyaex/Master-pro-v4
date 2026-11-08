"""سجل الإشارات القابلة للشرح (M4) — كل إشارة سجل كامل أو مرفوضة.

قاعدة صارمة: إشارة تفتقد أي حقل = مرفوضة. لا إشارة ناقصة تدخل النظام.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models import InstrumentClass, utcnow

REQUIRED_FIELDS = (
    "exchange", "symbol", "instrument_class", "direction", "entry", "stop_loss",
    "tp1", "tp2", "tp3", "risk_reward", "confidence", "evidence", "invalidation",
    "source_modules", "data_quality_flags", "ts",
)


class EvidenceItem(BaseModel):
    feature: str
    value: str
    explanation_ar: str      # شرح عربي مبسط لماذا دعم هذا العنصر الإشارة


class SignalRecord(BaseModel):
    exchange: str
    symbol: str
    instrument_class: InstrumentClass
    direction: str                       # long | short
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    confidence: float = Field(ge=0, le=100)
    evidence: list[EvidenceItem] = Field(min_length=1)
    invalidation: str                   # شرط أو سعر صريح يلغي الفكرة
    source_modules: list[str] = Field(min_length=1)
    data_quality_flags: list[str] = Field(default_factory=list)
    ts: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _complete(self) -> "SignalRecord":
        missing = [f for f in REQUIRED_FIELDS if getattr(self, f, None) in (None, "")]
        if missing:
            raise ValueError(f"إشارة مرفوضة — حقول ناقصة: {missing}")
        if self.entry <= 0 or self.stop_loss <= 0:
            raise ValueError("أسعار غير صالحة — إشارة مرفوضة")
        if self.direction == "long" and not (self.stop_loss < self.entry < self.tp1 <= self.tp2 <= self.tp3):
            raise ValueError("ترتيب مستويات غير منطقي لصفقة شراء — إشارة مرفوضة")
        if self.direction == "short" and not (self.stop_loss > self.entry > self.tp1 >= self.tp2 >= self.tp3):
            raise ValueError("ترتيب مستويات غير منطقي لصفقة بيع — إشارة مرفوضة")
        if self.risk_reward < 1.0:
            raise ValueError("نسبة عائد/مخاطرة أقل من 1 — مرفوضة")
        return self

    def explain_ar(self) -> str:
        lines = [
            f"إشارة {'شراء' if self.direction == 'long' else 'بيع'} على {self.symbol} في {self.exchange} "
            f"(فئة: {self.instrument_class.value}) — درجة الثقة {self.confidence:.0f}/100.",
            f"الدخول: {self.entry} | الوقف: {self.stop_loss} | الأهداف: {self.tp1} / {self.tp2} / {self.tp3} "
            f"| عائد/مخاطرة: {self.risk_reward:.2f}",
            "الأدلة:",
        ]
        lines += [f"  • {e.explanation_ar} ({e.feature} = {e.value})" for e in self.evidence]
        lines.append(f"شرط الإلغاء: {self.invalidation}")
        if self.data_quality_flags:
            lines.append(f"أعلام جودة البيانات: {', '.join(self.data_quality_flags)}")
        return "\n".join(lines)