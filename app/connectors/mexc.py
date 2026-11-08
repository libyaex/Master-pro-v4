"""موصل MEXC العام.

⚠️ MEXC futures العامة مقيدة بدون مفاتيح (ASSUMPTIONS A1) — degrade رشيد:
المصدر يظهر "غير متوفر" بسبب واضح. Spot فقط، وبحوذر معدل.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.connectors.base import PublicConnector, classify
from app.models import SourceHealth


class MEXCPublic(PublicConnector):
    exchange = "mexc"
    base_url = "https://api.mexc.com"

    async def discover(self) -> list[dict[str, Any]]:
        data = await self.get("/api/v3/exchangeInfo")
        out: list[dict[str, Any]] = []
        for s in data.get("symbols", []):
            if s.get("quoteAsset") != "USDT":
                continue
            out.append({
                "exchange": self.exchange, "symbol": s["symbol"], "base": s["baseAsset"],
                "quote": s["quoteAsset"],
                "instrument_class": classify(s["baseAsset"], s["quoteAsset"], s["symbol"]),
                "status": s.get("status", ""), "market_type": "spot",
                "tick_size": float(s.get("quoteAssetPrecision", 2) and 10 ** -s.get("quoteAssetPrecision", 2) or 0.01),
                "lot_step": float(10 ** -s.get("baseSizePrecision", 3)),
                "min_notional": 0.0, "contract_size": 1.0, "raw_symbol": s["symbol"],
            })
        return out

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list:
        raw = await self.get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [
            {
                "exchange": self.exchange, "symbol": symbol, "interval": interval,
                "open_time": datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                "close": float(r[4]), "volume": float(r[5]), "closed": True,
            }
            for r in raw
        ]


async def mexc_futures_unavailable() -> SourceHealth:
    """MEXC futures بدون مفاتيح = غير متوفر، دائماً وبصراحة."""
    return SourceHealth(
        source="mexc_futures", available=False,
        reason="واجهة MEXC futures العامة مقيدة بدون مفاتيح (راجع ASSUMPTIONS.md A1) — لا نقدّر بياناتها",
    )