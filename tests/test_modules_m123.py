# M1 التحكيم فقط — عزل الفشل
from datetime import timedelta

import pytest

from app.models import BookLevel, BookSnapshot, utcnow
from app.modules.m1_arbitrage import ArbitrageScanner, walk_book


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
