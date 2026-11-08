# M2 الحيتان + M3 الاختراق — v2 تشخيصي
import numpy as np
import pytest

from app.models import BookLevel, BookSnapshot, InstrumentClass, InstrumentSpec, Trade, utcnow
from app.modules.m2_whales import WhaleWatcher
from app.modules.m3_listing_breakout import ListingBreakoutScanner


def _book(exchange, bid, ask, ts=None):
    ts = ts or utcnow()
    return BookSnapshot(
        exchange=exchange, symbol="BTCUSDT", ts=ts,
        bids=[BookLevel(price=bid, size=2.0)], asks=[BookLevel(price=ask, size=2.0)],
    )


def test_whale_large_trade_detected():
    # تداول كبير واحد فقط يتجاوز الحد الأدنى 1M — نطاق 60 صغيراً كله 500$
    now = utcnow()
    trades = [Trade(exchange="x", symbol="BTCUSDT", trade_id=str(i), ts=now,
                    price=50000.0, size=0.01, aggressor="buy") for i in range(60)]
    trades.append(Trade(exchange="x", symbol="BTCUSDT", trade_id="big", ts=now,
                        price=50000.0, size=50.0, aggressor="buy"))
    w = WhaleWatcher(min_notional_usd=1_000_000)
    events = w.detect_large_trades(trades)
    assert len(events) == 1
    assert events[0].notional_usd == pytest.approx(2_500_000)


def test_whale_unfilled_order_not_a_trade():
    w = WhaleWatcher()
    prev = _book("x", 100.0, 101.0)
    curr = BookSnapshot(exchange="x", symbol="BTCUSDT", ts=utcnow(),
                        bids=[BookLevel(price=100.0, size=2.0)],
                        asks=[BookLevel(price=102.0, size=5000.0)])
    events = w.book_events(prev, curr, big_size=1000.0)
    assert events
    assert all("unconfirmed" in e.label for e in events)
    flags = w.detect_spoof_possible(events, fills_at_prices=set())
    assert any("ممكن" in f for f in flags)


def test_accumulation_from_executed_flow():
    now = utcnow()
    buys = [Trade(exchange="x", symbol="BTCUSDT", trade_id=f"b{i}", ts=now,
                  price=100.0, size=1.0, aggressor="buy") for i in range(40)]
    sells = [Trade(exchange="x", symbol="BTCUSDT", trade_id=f"s{i}", ts=now,
                   price=100.0, size=0.1, aggressor="sell") for i in range(10)]
    w = WhaleWatcher()
    sig = w.accumulation(buys + sells)
    assert sig.direction == "accumulation"


def fixture_spec(symbol="NEWUSDT"):
    return InstrumentSpec(
        exchange="binance_spot", symbol=symbol, base=symbol[:-4], quote="USDT",
        instrument_class=InstrumentClass.CRYPTO, status="TRADING", market_type="spot",
        tick_size=0.01, lot_step=0.0001, min_notional=5.0,
    )


def test_listing_diff_detects_new():
    sc = ListingBreakoutScanner()
    first = sc.diff_new_listings([fixture_spec("NEWUSDT")])
    assert len(first) == 1
    again = sc.diff_new_listings([fixture_spec("NEWUSDT")])
    assert len(again) == 0


def test_breakout_requires_close_and_volume():
    sc = ListingBreakoutScanner()
    n = 120
    closes = np.full(n, 100.0); highs = np.full(n, 101.0); lows = np.full(n, 99.0)
    vols = np.full(n, 10.0)
    closes[-1] = 103.0; highs[-1] = 103.5; vols[-1] = 50.0
    ev = sc.breakout("x", "TESTUSDT", closes, highs, lows, vols,
                     depth_usd=100_000, spread_pct=0.01)
    assert ev is not None and ev.direction == "up"


def test_wick_alone_is_not_breakout():
    sc = ListingBreakoutScanner()
    n = 120
    closes = np.full(n, 100.0); highs = np.full(n, 101.0); lows = np.full(n, 99.0)
    vols = np.full(n, 10.0)
    highs[-1] = 110.0
    ev = sc.breakout("x", "TESTUSDT", closes, highs, lows, vols,
                     depth_usd=100_000, spread_pct=0.01)
    assert ev is None


def test_suspicious_pump_blocked():
    sc = ListingBreakoutScanner()
    n = 120
    closes = np.full(n, 100.0); highs = np.full(n, 101.0); lows = np.full(n, 99.0)
    vols = np.full(n, 10.0)
    closes[-1] = 130.0; vols[-1] = 80.0
    ev = sc.breakout("x", "TESTUSDT", closes, highs, lows, vols,
                     depth_usd=5_000, spread_pct=0.5,
                     cross_exchange_moves={"other": -0.01})
    assert ev is not None
    assert ev.risk_label == "suspicious"
    assert ("x", "TESTUSDT") in sc.blocklist
