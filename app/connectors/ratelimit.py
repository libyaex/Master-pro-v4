"""محدد معدل token-bucket لكل منصة + backoff أسّي بـ jitter.

لا نزعم وصولاً غير محدود: الأرقام متحفظة وموثقة في COST_AUDIT.md.
الأولوية: البث الحي (WS) أولاً، ثم REST للتحليل، والـ backfill أخيراً.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    name: str
    rate: float            # طلبات/ثانية
    capacity: int
    tokens: float = field(default=0.0)
    updated: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens <= 0:
            self.tokens = float(self.capacity)

    def take(self, n: int = 1) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    async def acquire(self, n: int = 1) -> None:
        while not self.take(n):
            need = (n - self.tokens) / self.rate
            await asyncio.sleep(max(0.01, need) + random.uniform(0, 0.02))


@dataclass
class Backoff:
    base: float = 0.5
    factor: float = 2.0
    max_wait: float = 60.0
    _n: int = 0

    def next(self) -> float:
        wait = min(self.max_wait, self.base * (self.factor ** self._n))
        self._n += 1
        return wait + random.uniform(0, wait * 0.1)   # jitter

    def reset(self) -> None:
        self._n = 0


# حدود متحفظة لكل منصة — توثيق كامل في ASSUMPTIONS.md A1
RATE_LIMITS: dict[str, float] = {
    "binance_spot": 10.0,
    "binance_futures": 10.0,
    "bybit": 8.0,
    "bybit_linear": 8.0,
    "okx": 6.0,
    "mexc": 5.0,
    "coingecko": 0.1,      # ~6/دقيقة
}


class RateLimiterRegistry:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def bucket(self, name: str) -> TokenBucket:
        if name not in self._buckets:
            rate = RATE_LIMITS.get(name, 5.0)
            self._buckets[name] = TokenBucket(name=name, rate=rate, capacity=max(1, int(rate)))
        return self._buckets[name]

    async def acquire(self, name: str) -> None:
        await self.bucket(name).acquire()

    def used_ratio(self, name: str) -> float:
        b = self._buckets.get(name)
        if not b:
            return 0.0
        return 1.0 - (b.tokens / b.capacity)


registry = RateLimiterRegistry()