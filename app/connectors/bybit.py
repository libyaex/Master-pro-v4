"""موصل Bybit v5 العام — spot وderivatives. لا مفاتيح."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.connectors.base import PublicConnector, classify
from app.models import Candle


def _interval_delta(interval: str) -> timedelta:
    """مدة الشمعة — Bybit لا يرجع وقت الإغلاق، فنحسبه من الفاصل."""
    if interval.isdigit():
        return timedelta(minutes=int(interval))
    return {"D": timedelta(days=1), "W": timedelta(weeks=1), "M": timedelta(days=30)}.get(interval, timedelta(0))


class BybitSpot(PublicConnector):
    exchange = "bybit"
    base_url = "https://api.bybit.com"

    async def discover(self) -> list[dict[str, Any]]:
        data = await self.get("/v5/market/instruments-info", {"category": "spot"})
        out: list[dict[str, Any]] = []
        for s in data.get("result", {}).get("list", []):
            base, quote = s.get("baseCoin", ""), s.get("quoteCoin", "")
            if quote != "USDT":
                continue
            out.append({
                "exchange": self.exchange, "symbol": s["symbol"], "base": base, "quote": quote,
                "instrument_class": classify(base, quote, s["symbol"]),
                "status": s.get("status", ""), "market_type": "spot",
                "tick_size": float(s.get("priceFilter", {}).get("tickSize", 0.01) or 0.01),
                "lot_step": float(s.get("lotSizeFilter", {}).get("qtyStep", 0.001) or 0.001),
                "min_notional": float(s.get("lotSizeFilter", {}).get("minOrderAmt", 1.0) or 1.0),
                "contract_size": 1.0, "raw_symbol": s["symbol"],
            })
        return out

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list[Candle]:
        raw = await self.get("/v5/market/kline", {
            "category": "spot", "symbol": symbol, "interval": interval, "limit": limit,
        })
        rows = raw.get("result", {}).get("list", [])
        # Bybit يرجع الأحدث أولاً — نقلبها تصاعدياً
        rows = list(reversed(rows))
        return [
            Candle(
                exchange=self.exchange, symbol=symbol, interval=interval,
                open_time=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc) + _interval_delta(interval),
                open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                volume=float(r[5]), closed=True,
            )
            for r in rows
        ]


class BybitLinear(BybitSpot):
    """derivatives USDT-perpetual — نفس واجهة v5 بفئة linear."""
    exchange = "bybit_linear"   # اسم مستقل كي لا يطمس (exchange, symbol) أدوات spot

    async def discover(self) -> list[dict[str, Any]]:
        data = await self.get("/v5/market/instruments-info", {"category": "linear"})
        out: list[dict[str, Any]] = []
        for s in data.get("result", {}).get("list", []):
            base, quote = s.get("baseCoin", ""), s.get("quoteCoin", "")
            if quote != "USDT" or s.get("contractType") not in ("LinearPerpetual", None, ""):
                if quote != "USDT":
                    continue
            out.append({
                "exchange": self.exchange, "symbol": s["symbol"], "base": base, "quote": quote,
                "instrument_class": classify(base, quote, s["symbol"]),
                "status": s.get("status", ""), "market_type": "perpetual",
                "tick_size": float(s.get("priceFilter", {}).get("tickSize", 0.01) or 0.01),
                "lot_step": float(s.get("lotSizeFilter", {}).get("qtyStep", 0.001) or 0.001),
                "min_notional": float(s.get("lotSizeFilter", {}).get("minNotionalValue", 0.0) or 0.0),
                "contract_size": float(s.get("contractMultiplier", 1) or 1),
                "raw_symbol": s["symbol"],
            })
        return out

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list[Candle]:
        raw = await self.get("/v5/market/kline", {
            "category": "linear", "symbol": symbol, "interval": interval, "limit": limit,
        })
        rows = list(reversed(raw.get("result", {}).get("list", [])))
        return [
            Candle(
                exchange=self.exchange, symbol=symbol, interval=interval,
                open_time=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc) + _interval_delta(interval),
                open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                volume=float(r[5]), closed=True,
            )
            for r in rows
        ]

    async def funding(self, symbol: str) -> dict:
        return await self.get("/v5/market/tickers", {"category": "linear", "symbol": symbol})

    async def open_interest(self, symbol: str) -> dict:
        return await self.get("/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": "5min"})