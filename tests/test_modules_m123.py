# اختبارات الوحدات M1/M2/M3 — التحكيم، الحيتان، الاختراق (split part 2)
import numpy as np
import pytest
from datetime import timedelta

from app.models import BookLevel, BookSnapshot, InstrumentClass, InstrumentSpec, Trade, utcnow
from app.modules.m1_arbitrage import ArbitrageScanner, walk_book
from app.modules.m2_whales import WhaleWatcher
from app.modules.m3_listing_breakout import ListingBreakoutScanner


def fixture_spec(symbol="BTCUSDT", exchange="binance_spot", cls=InstrumentClass.CRYPTO):
    return InstrumentSpec(
        exchange=exchange, symbol=symbol, base=symbol[:-4], quote="USDT",
        instrument_class=cls, status="TRADING", market_type="spot",
        tick_size=0.01, lot_step=0.0001, min_notional=5.0,
    )


def _book(exchange, bid, ask, ts=None):
    ts = ts or utcnow()
    return BookSnapshot(
        exchange=exchange, symbol="BTCUSDT", ts=ts,
        bids=[BookLevel(price=bid, size=2.0)], asks=[BookLevel(price=ask, size=2.0)],
    )


def test_walk_book_real_depth():
    levels = [BookLevel(price=100.0, size=1.0), BookLevel(price=101.0, size=1.0)]
    r = walk_book(levels, 1.5)
    assert r.avg_price == pytest.approx((100.0 * 1.0 + 101.0 * 0.5) / 1.5)
    assert r.sufficient_depth


def test_walk_book_insufficient_depth():
    r = walk_book([BookLevel(price=100.0, size=0.5)], 2.0)
    assert not r.sufficient_depth


def test_arb_executable_needs_fees_known():
    scanner = ArbitrageScanner(taker_fees={"binance_spot": 0.0004, "bybit": 0.0004})
    opp = scanner.compare("BTC", "USDT", "crypto", 1.0, {
        "binance_spot": _book("binance_spot", 99.0, 100.0),
        "bybit": _book("bybit", 100.5, 101.0),
    }, recent_volatility=0.00001)
    assert opp is not None
    assert opp.classification == "informational"


def test_arb_executable_when_pair_enabled():
    scanner = ArbitrageScanner(
        taker_fees={"binance_spot": 0.0004, "bybit": 0.0004},
        auto_execute_pairs={("binance_spot", "bybit")},
    )
    opp = scanner.compare("BTC", "USDT", "crypto", 1.0, {
        "binance_spot": _book("binance_spot", 99.0, 100.0),
        "bybit": _book("bybit", 100.5, 101.0),
    }, recent_volatility=0.00001)
    assert opp is not None
    assert opp.classification == "executable"


def test_arb_stale_book_excluded():
    scanner = ArbitrageScanner(taker_fees={"a": 0.0, "b": 0.0}, freshness_ms=10)
    old = utcnow() - timedelta(seconds=5)
    opp = scanner.compare("BTC", "USDT", "crypto", 1.0, {
        "a": _book("a", 99.0, 100.0, old),
        "b": _book("b", 100.5, 101.0),
    }, recent_volatility=0.0)
    assert opp is None


def test_arb_missing_fees_informational():
    scanner = ArbitrageScanner(taker_fees={"a": 0.0004})
    opp = scanner.compare("BTC", "USDT", "crypto", 1.0, {
        "a": _book("a", 99.0, 100.0),
        "b": _book("b", 100.5, 101.0),
    }, recent_volatility=0.0)
    assert opp is not None
    assert opp.classification == "informational"
    assert "رسوم" in opp.reason_ar


def test_whale_large_trade_detected():
    now = utcnow()
    trades = [Trade(exchange="x", symbol="BTCUSDT", trade_id=str(i), ts=now,
                    price=50000.0, size=0.01, aggressor="buy") for i in range(60)]
    trades.append(Trade(exchange="x", symbol="BTCUSDT", trade_id="big", ts=now,
                        price=50000.0, size=50.0, aggressor="buy"))
    w = WhaleWatcher(min_notional_usd=1_000_000)
    events = w.detect_large_trades(trades)
    assert len(events) == 1 and events[0].notional_usd == pytest.approx(2_500_000)


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
    assert ev.risk_label == "suspicious"
    assert ("x", "TESTUSDT") in sc.blocklist
