"""محرك البيانات الموحد — استكشاف ديناميكي، كشف إدراجات/إلغاءات، صحة المصادر.

قاعدة صارمة: مصدر متوقف/مقيّد = "غير متوفر" بسبب + آخر طابع صالح. لا تقدير صامت.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.connectors.base import normalize_status
from app.connectors.binance import BinanceFutures, BinanceSpot
from app.connectors.bybit import BybitLinear, BybitSpot
from app.connectors.mexc import MEXCPublic, mexc_futures_unavailable
from app.connectors.okx import OKXPublic
from app.models import InstrumentSpec, SourceHealth, utcnow

log = logging.getLogger("engine")


@dataclass
class DiscoveryDiff:
    new_listings: list[InstrumentSpec] = field(default_factory=list)
    delistings: list[InstrumentSpec] = field(default_factory=list)


class MarketEngine:
    def __init__(self) -> None:
        self.connectors: dict[str, Any] = {
            "binance_spot": BinanceSpot(),
            "binance_futures": BinanceFutures(),
            "bybit": BybitSpot(),
            "bybit_linear": BybitLinear(),
            "okx": OKXPublic(),
            "mexc": MEXCPublic(),
        }
        self.instruments: dict[str, InstrumentSpec] = {}   # (exchange,symbol) -> spec
        self.health: dict[str, SourceHealth] = {}

    async def refresh_discovery(self) -> DiscoveryDiff:
        """استكشاف متزامن لكل المنصات؛ الفشل = مصدر غير متوفر (لا يُفشل الكل)."""
        diff = DiscoveryDiff()
        tasks = {name: conn.discover() for name, conn in self.connectors.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        seen: set[tuple[str, str]] = set()
        refreshed: set[str] = set()   # المنصات التي نجح استكشافها هذه الجولة فقط
        for (name, _), res in zip(tasks.items(), results):
            if isinstance(res, Exception):
                reason = str(res)
                prev = self.health.get(name)
                self.health[name] = SourceHealth(
                    source=name, available=False, reason=reason,
                    last_good_ts=prev.last_good_ts if prev else None,
                )
                log.warning("مصدر غير متوفر: %s — %s", name, reason)
                continue
            self.health[name] = SourceHealth(source=name, available=True, last_good_ts=utcnow())
            refreshed.add(self.connectors[name].exchange)
            for row in res:
                key = (row["exchange"], row["symbol"])
                seen.add(key)
                row = {**row, "status": normalize_status(row.get("status", ""))}
                spec = InstrumentSpec(**row)
                if key not in self.instruments:
                    diff.new_listings.append(spec)
                self.instruments[key] = spec

        # إلغاءات الإدراج — فقط للمنصات التي نجح استكشافها؛ فشل مصدر مؤقت ليس إلغاء إدراج
        for key, spec in list(self.instruments.items()):
            if key not in seen and spec.exchange in refreshed:
                diff.delistings.append(spec)
                del self.instruments[key]

        self.health["mexc_futures"] = await mexc_futures_unavailable()
        self.health["xauusd_spot"] = SourceHealth(
            source="xauusd_spot", available=False,
            reason="لا يوجد مصدر عام مجاني موثوق لسعر الذهب الفوري بلا مفاتيح",
        )
        self.health["onchain_public"] = SourceHealth(
            source="onchain_public", available=False,
            reason="لا يوجد مصدر on-chain رسمي مجاني يسمح الاستخدام بلا مفاتيح حالياً",
        )
        return diff

    def class_members(self, cls_name: str) -> list[InstrumentSpec]:
        return [s for s in self.instruments.values() if s.instrument_class.value == cls_name and s.status == "TRADING"]

    def health_list(self) -> list[SourceHealth]:
        return list(self.health.values())

    def get(self, exchange: str, symbol: str) -> InstrumentSpec | None:
        return self.instruments.get((exchange, symbol))

    def same_class_match(self, base: str, quote: str, cls: str) -> list[InstrumentSpec]:
        """أدوات متطابقة فقط: نفس الأصل + الفئة + عملة التسعير. لا خلط فئات أبداً."""
        return [
            s for s in self.instruments.values()
            if s.base == base and s.quote == quote
            and s.instrument_class.value == cls and s.status == "TRADING"
        ]