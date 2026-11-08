"""موصل OKX v5 العام — spot وderivatives. لا مفاتيح."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.connectors.base import PublicConnector, classify
from app.models import Candle


class OKXPublic(PublicConnector):
    exchange = "okx"
    base_url = "https://www.okx.com"

    async def discover(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for inst_type in ("SPOT", "SWAP"):
            data = await self.get("/api/v5/public/instruments", {"instType": inst_type})
            for s in data.get("data", []):
                base, quote = s.get("baseCcy", s.get("instId", "").split("-")[0]), s.get("quoteCcy", "USDT")
                if inst_type == "SWAP":
                    base = s.get("instId", "").split("-")[0]
                    quote = s.get("settleCcy", "USDT")
                if quote != "USDT":
                    continue
                out.append({
                    "exchange": self.exchange, "symbol": s["instId"], "base": base, "quote": quote,
                    "instrument_class": classify(base, quote, s["instId"]),
                    "status": s.get("state", ""), "market_type": "spot" if inst_type == "SPOT" else "perpetual",
                    "tick_size": float(s.get("tickSz", "0.01")),
                    "lot_step": float(s.get("lotSz", s.get("ctVal", "0.001"))),
                    "min_notional": 0.0, "contract_size": float(s.get("ctVal", 1) or 1),
                    "raw_symbol": s["instId"],
                })
        return out

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list[Candle]:
        # OKX: bar مثل 1m,5m,1H,4H,1D وسلسلة نصية
        raw = await self.get("/api/v5/market/candles", {"instId": symbol, "bar": interval, "limit": min(limit, 300)})
        rows = list(reversed(raw.get("data", [])))
        return [
            Candle(
                exchange=self.exchange, symbol=symbol, interval=interval,
                open_time=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(int(r[0]) / 1000 + 1, tz=timezone.utc),
                open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                volume=float(r[5]), closed=r[8] == "1" if len(r) > 8 else True,
            )
            for r in rows
        ]