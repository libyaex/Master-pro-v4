# M2 الحيتان
import pytest

from app.models import BookLevel, BookSnapshot, Trade, utcnow
from app.modules.m2_whales import WhaleWatcher


def _book(exchange, bid, ask, ts=None):
    ts = ts or utcnow()
    return BookSnapshot(
        exchange=exchange, symbol="BTCUSDT", ts=ts,
        bids=[BookLevel(price=bid, size=2.0)], asks=[BookLevel(price=ask, size=2.0)],
    )


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


# M3 الاختراق
def test_listing_diff_detects_new():
    from app.models import InstrumentClass, InstrumentSpec

    def fixture_spec(symbol="NEWUSDT"):
        return InstrumentSpec(
            exchange="binance_spot", symbol=symbol, base=symbol[:-4], quote="USDT",
            instrument_class=InstrumentClass.CRYPTO, status="TRADING", market_type="spot",
            tick_size=0.01, lot_step=0.0001, min_notional=5.0,
        )

    sc = ListingBreakoutScanner()
    first = sc.diff_new_listings([fixture_spec("NEWUSDT")])
    assert len(first) == 1
    again = sc.diff_new_listings([fixture_spec("NEWUSDT")])
    assert len(again) == 0


from app.modules.m3_listing_breakout import ListingBreakoutScanner  # noqa: E402