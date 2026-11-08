"""موصل عام أساسي — لا مفاتيح، بيانات عامة فقط، بحدود معدل وتراجع."""
from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.connectors.ratelimit import Backoff, registry
from app.models import SourceHealth

log = logging.getLogger("connectors")

TOKENIZED_GOLD = {"PAXG", "XAUT"}


class PublicConnector(ABC):
    """قاعدة موصل عام. لا يطلب مفاتيح أبداً لأي نقطة عامة."""

    exchange: str = ""

    def __init__(self) -> None:
        self.limiter_name = self.exchange
        self.backoff = Backoff()
        self.health = SourceHealth(source=self.exchange, available=True)
        self._client: httpx.AsyncClient | None = None

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: dict | None = None) -> Any:
        """GET مع محدد معدل + backoff أسّي بـ jitter. يرمي عند فشل دائم."""
        await registry.acquire(self.limiter_name)
        client = await self.client()
        url = f"{self.base_url}{path}"
        for attempt in range(6):
            try:
                r = await client.get(url, params=params or {})
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if r.status_code in (403, 451):
                    # المنصة تقيد هذا المصدر — degrade رشيد، مصدر غير متوفر وليس خطأ
                    self.health = SourceHealth(
                        source=self.exchange, available=False,
                        reason=f"HTTP {r.status_code}: المصدر مقيد بدون مفاتيح",
                        last_good_ts=self.health.last_good_ts,
                    )
                    raise RuntimeError(self.health.reason)
                r.raise_for_status()
                self.backoff.reset()
                return r.json()
            except (httpx.HTTPError, RuntimeError) as exc:
                wait = self.backoff.next()
                log.warning("%s: %s محاولة %d — تراجع %.1fs", self.exchange, exc, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(f"{self.exchange}: فشل نهائي بعد المحاولات — مصدر غير متوفر مؤقتاً")

    @abstractmethod
    async def discover(self) -> list[dict[str, Any]]: ...


def classify(base: str, quote: str, symbol: str) -> str:
    """فئات الأدوات الأربع — لا تُخلط أبداً في المقارنات."""
    from app.models import InstrumentClass

    if base in TOKENIZED_GOLD:
        return InstrumentClass.TOKENIZED_GOLD
    if symbol.startswith("XAU") and quote in ("USD", "USDT", "USDC"):
        return InstrumentClass.GOLD_DERIVATIVE
    if base == "XAUUSD":
        return InstrumentClass.SPOT_XAUUSD
    return InstrumentClass.CRYPTO