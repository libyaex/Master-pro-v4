"""شواهد موصلات — استجابات REST مسجلة موسومة fixture. لا استدعاءات شبكة في الاختبارات."""
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURES.mkdir(exist_ok=True)

# شواهد استجابات عامة مسجلة — موسومة، لاختبار المحللات فقط
BINANCE_EXCHANGEINFO_SAMPLE = {
    "symbols": [
        {
            "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00010000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        }
    ]
}

BYBIT_INSTRUMENTS_SAMPLE = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "symbol": "BTCUSDT", "status": "Trading", "baseCoin": "BTC", "quoteCoin": "USDT",
                "priceFilter": {"tickSize": "0.01"},
                "lotSizeFilter": {"qtyStep": "0.0001", "minOrderQty": "0.0001", "minOrderAmt": "1"},
            }
        ]
    },
}

MEXC_KLINES_SAMPLE = [
    [1700000000000, "42000.0", "42100.0", "41900.0", "42050.0", "123.45", 1700000059999]
]