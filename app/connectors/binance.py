"""موصل Binance العام — Spot وFutures. بيانات عامة فقط، لا مفاتيح."""
from __future__ import annotations

from typing import Any

from app.connectors.base import PublicConnector, classify
from app.models import Candle, InstrumentClass, InstrumentSpec, utcnow


class BinanceSpot(PublicConnector):
    exchange = "binance_spot"
    base_url = "https://api.binance.com"

    async def discover(self) -> list[dict[str, Any]]:
        data = await self.get("/api/v3/exchangeInfo")
        out: list[dict[str, Any]] = []
        for s in data.get("symbols", []):
            if s.get("quoteAsset") != "USDT":
                continue
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            tick = float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01) or 0.01)
            lot = float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001) or 0.001)
            notional = float(filters.get("NOTIONAL", {}).get("minNotional", 5.0) or 5.0)
            out.append({
                "exchange": self.exchange, "symbol": s["symbol"], "base": s["baseAsset"],
                "quote": s["quoteAsset"],
                "instrument_class": classify(s["baseAsset"], s["quoteAsset"], s["symbol"]),
                "status": s.get("status", ""), "market_type": "spot",
                "tick_size": tick, "lot_step": lot, "min_notional": notional,
                "contract_size": 1.0, "raw_symbol": s["symbol"],
            })
        return out

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list[Candle]:
        raw = await self.get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [_binance_candle(r, self.exchange, symbol, interval) for r in raw]

    async def depth(self, symbol: str, limit: int = 100) -> dict:
        return await self.get("/api/v3/depth", {"symbol": symbol, "limit": limit})

    async def trades(self, symbol: str, limit: int = 500) -> list[dict]:
        return await self.get("/api/v3/trades", {"symbol": symbol, "limit": limit})


class BinanceFutures(BinanceSpot):
    exchange = "binance_futures"
    base_url = "https://fapi.binance.com"

    async def discover(self) -> list[dict[str, Any]]:
        data = await self.get("/fapi/v1/exchangeInfo")
        out: list[dict[str, Any]] = []
        for s in data.get("symbols", []):
            if s.get("quoteAsset") != "USDT":
                continue
            tick = float(s.get("filters", [{}])[0].get("tickSize", 0.01) or 0.01)
            out.append({
                "exchange": self.exchange, "symbol": s["symbol"], "base": s["baseAsset"],
                "quote": s["quoteAsset"],
                "instrument_class": classify(s["baseAsset"], s["quoteAsset"], s["symbol"]),
                "status": s.get("status", ""), "market_type": s.get("contractType", "PERPETUAL"),
                "tick_size": tick, "lot_step": float(s.get("stepSize", 0.001)),
                "min_notional": float(s.get("filters", [{}])[0].get("minNotional", 5.0)),
                "contract_size": float(s.get("contractType") == "PERPETUAL" and 1 or 1),
                "raw_symbol": s["symbol"],
            })
        return out

    async def funding(self, symbol: str) -> dict:
        return await self.get("/fapi/v1/premiumIndex", {"symbol": symbol})

    async def open_interest(self, symbol: str) -> dict:
        return await self.get("/fapi/v1/openInterest", {"symbol": symbol})


def _binance_candle(row: list, exchange: str, symbol: str, interval: str) -> Candle:
    from datetime import datetime, timezone

    return Candle(
        exchange=exchange, symbol=symbol, interval=interval,
        open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
        close_time=datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc),
        open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
        volume=float(row[5]), quote_volume=float(row[7]),
        closed=True,
    )