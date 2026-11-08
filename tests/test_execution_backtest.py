# اختبارات التنفيذ: الطابور، التعبئة الورقية، backtest، فئات الأدوات (split part 4)
import numpy as np
import pytest
from pathlib import Path

from app.models import BookLevel, BookSnapshot, InstrumentClass, utcnow
from app.execution.queue import DurableQueue, OrderState, OrderTracker
from app.execution.paper_fill import PaperFillModel
from app.strategies.plugins import StrategyContext, TrendPullback
from app.backtest.engine import Backtester
from app.risk.gate import RiskGate

REPO_ROOT = Path(__file__).parent.parent


def fixture_candles(n=300, start=50000.0, drift=0.0002, seed=7):
    rng = np.random.default_rng(seed)
    closes = start * np.cumprod(1 + rng.normal(drift, 0.004, n))
    highs = closes * (1 + np.abs(rng.normal(0, 0.002, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.002, n)))
    vols = np.abs(rng.normal(1000, 200, n))
    return closes, highs, lows, vols


def _book(exchange, bid, ask):
    return BookSnapshot(
        exchange=exchange, symbol="BTCUSDT", ts=utcnow(),
        bids=[BookLevel(price=bid, size=2.0)], asks=[BookLevel(price=ask, size=2.0)],
    )


def test_queue_priority_kill_switch_first(tmp_path):
    q = DurableQueue(str(tmp_path / "q.db"))
    q.enqueue("backfill", {})
    q.enqueue("analysis", {})
    q.enqueue("kill_switch", {})
    job = q.lease_next()
    assert job.kind == "kill_switch"
    q.complete(job.id)
    assert q.lease_next().kind == "analysis"


def test_queue_idempotency_no_duplicate(tmp_path):
    q = DurableQueue(str(tmp_path / "q.db"))
    a = q.enqueue("analysis", {"x": 1}, idem="same-key")
    b = q.enqueue("analysis", {"x": 1}, idem="same-key")
    assert b is None


def test_queue_lease_expiry_requeued(tmp_path):
    import time

    q = DurableQueue(str(tmp_path / "q.db"))
    q.enqueue("analysis", {"x": 1})
    q.lease_next(lease_seconds=0.01)
    time.sleep(0.05)
    job = q.lease_next()
    assert job is not None and job.kind == "analysis"


def test_order_state_machine_valid_transitions():
    q = OrderTracker.__new__(OrderTracker)
    q.orders = {}
    oid = "o1"
    assert q.transition(oid, OrderState.SUBMITTED)
    assert q.transition(oid, OrderState.FILLED)
    assert not q.transition(oid, OrderState.OPEN)


def test_reconciliation_exchange_is_truth():
    t = OrderTracker.__new__(OrderTracker)
    t.orders = {"o1": OrderState.PENDING, "o2": OrderState.OPEN}
    fixes = t.reconcile({"o1": OrderState.FILLED, "o2": OrderState.OPEN})
    assert "o1" in fixes and t.orders["o1"] == OrderState.FILLED


def test_paper_fill_from_real_book():
    book = _book("x", 99.9, 100.0)
    model = PaperFillModel(taker_fee_pct=0.1)
    fill = model.simulate_fill(book, "buy", 1.0, "x", "BTCUSDT")
    assert fill.filled_price == pytest.approx(100.0)
    assert fill.fee == pytest.approx(0.1)
    assert fill.slippage_pct == pytest.approx(0.0)


def test_paper_fill_no_depth_no_fill():
    book = BookSnapshot(exchange="x", symbol="BTCUSDT", ts=utcnow(), bids=[], asks=[])
    model = PaperFillModel()
    assert model.simulate_fill(book, "buy", 1.0, "x", "BTCUSDT") is None


def test_backtester_insufficient_history_says_so():
    closes, highs, lows, vols = fixture_candles(n=100)
    ctx = StrategyContext("x", "BTCUSDT", "crypto", closes, highs, lows, vols, 0.02, 100_000)
    bt = Backtester()
    rep = bt.run(TrendPullback(), ctx, RiskGate())
    assert rep.insufficient_history
    assert "لا نتائج تُختلق" in rep.note_ar


def test_backtester_runs_and_reports():
    closes, highs, lows, vols = fixture_candles(n=400)
    ctx = StrategyContext("x", "BTCUSDT", "crypto", closes, highs, lows, vols, 0.02, 100_000)
    bt = Backtester()
    rep = bt.run(TrendPullback(), ctx, RiskGate())
    assert not rep.insufficient_history
    assert rep.trades >= 0


def test_monte_carlo_outputs():
    bt = Backtester()
    res = bt.monte_carlo(np.array([1.0, -0.5, 1.2, -0.3, 0.8]), iters=200)
    assert res["ok"] and res["final_p50"] != res["final_p95"]


def test_instrument_classes_never_mixed():
    from app.connectors.base import classify
    assert classify("PAXG", "USDT", "PAXGUSDT") == InstrumentClass.TOKENIZED_GOLD.value
    assert classify("BTC", "USDT", "BTCUSDT") == InstrumentClass.CRYPTO.value
    assert classify("XAU", "USDT", "XAUUSD_USDT") == InstrumentClass.GOLD_DERIVATIVE.value


def test_no_guaranteed_profit_claims_in_code():
    banned = ("ضمان الربح", "guaranteed profit", "أرباح مضمونة", "ربح مضمون")
    for path in list((REPO_ROOT / "app").rglob("*.py")) + [REPO_ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"عبارة ممنوعة في {path}: {phrase}"
