# v2 — CI re-verify after all init/import fixes
"""اختبارات شاملة — بيانات شواهد موسومة فقط (fixture)، لا بيانات مختلقة في مسارات الإنتاج."""
import math
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from app.engine import indicators as ind
from app.models import (BookLevel, BookSnapshot, InstrumentClass,
                        InstrumentSpec, Trade, utcnow)
from app.execution.queue import DurableQueue, OrderState, OrderTracker
from app.modules.m1_arbitrage import ArbitrageScanner, walk_book
from app.modules.m2_whales import WhaleWatcher
from app.modules.m3_listing_breakout import ListingBreakoutScanner
from app.modules.m5_adaptive import AdaptiveEvaluator, walk_forward_sanity
from app.modules.m6_ranking import OpportunityRanker
from app.risk.gate import OrderRequest, RiskConfig, RiskGate
from app.strategies.signal_record import EvidenceItem, SignalRecord
from app.strategies.plugins import StrategyContext, TrendPullback
from app.execution.paper_fill import PaperFillModel
from app.backtest.engine import Backtester

FIXTURE_TAG = "fixture"  # كل بيانات هذه الملفات شواهد اختبار موسومة
REPO_ROOT = Path(__file__).parent.parent


def fixture_spec(symbol="BTCUSDT", exchange="binance_spot", cls=InstrumentClass.CRYPTO):
    return InstrumentSpec(
        exchange=exchange, symbol=symbol, base=symbol[:-4], quote="USDT",
        instrument_class=cls, status="TRADING", market_type="spot",
        tick_size=0.01, lot_step=0.0001, min_notional=5.0,
    )


def fixture_candles(n=300, start=50000.0, drift=0.0002, seed=7):
    """بيانات شبه-عشوائية موسومة fixture — للاختبارات فقط، لا تدخل الإنتاج."""
    rng = np.random.default_rng(seed)
    closes = start * np.cumprod(1 + rng.normal(drift, 0.004, n))
    highs = closes * (1 + np.abs(rng.normal(0, 0.002, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.002, n)))
    vols = np.abs(rng.normal(1000, 200, n))
    return closes, highs, lows, vols


# ---------- مؤشرات ----------
def test_ema_matches_manual():
    vals = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    e = ind.ema(vals, 3)
    assert math.isnan(e[1])
    assert e[2] == pytest.approx(2.0)
    assert e[3] == pytest.approx(3.0)


def test_rsi_bounds_and_trend():
    c = np.linspace(100, 200, 50)
    r = ind.rsi(c)
    assert not math.isnan(r[-1]) and r[-1] > 70  # صعود قوي = RSI مرتفع


def test_atr_positive():
    closes, highs, lows, _ = fixture_candles()
    a = ind.atr(highs, lows, closes)
    assert not math.isnan(a[-1]) and a[-1] > 0


def test_regime_detector():
    closes, highs, lows, _ = fixture_candles()
    a = ind.adx(highs, lows, closes)
    atrv = ind.atr(highs, lows, closes)
    regime = ind.market_regime(closes, a, atrv)
    assert regime in ("trending", "ranging", "high_volatility", "unknown")


# ---------- M4: سجل الإشارة ----------
def _make_signal(direction="long", entry=100.0, stop=95.0, tp1=110.0):
    return SignalRecord(
        exchange="binance_spot", symbol="BTCUSDT", instrument_class=InstrumentClass.CRYPTO,
        direction=direction, entry=entry, stop_loss=stop,
        tp1=tp1, tp2=tp1 + 10, tp3=tp1 + 20, risk_reward=2.0, confidence=75,
        evidence=[EvidenceItem(feature="trend", value="up", explanation_ar="اختبار fixture")],
        invalidation="إغلاق تحت 95", source_modules=["trend_pullback"],
    )


def test_signal_complete_record_accepted():
    s = _make_signal()
    assert "شراء" in s.explain_ar()


def test_signal_missing_field_rejected():
    with pytest.raises(ValueError):
        SignalRecord(
            exchange="binance_spot", symbol="BTCUSDT", instrument_class=InstrumentClass.CRYPTO,
            direction="long", entry=100.0, stop_loss=95.0,
            tp1=110.0, tp2=120.0, tp3=0.0,   # tp3 ناقص
            risk_reward=2.0, confidence=75,
            evidence=[EvidenceItem(feature="t", value="1", explanation_ar="f")],
            invalidation="x", source_modules=["s"],
        )


def test_signal_bad_level_order_rejected():
    with pytest.raises(ValueError):
        _make_signal(entry=100.0, stop=105.0)  # شراء بوقف فوق الدخول


# ---------- M1: التحكيم ----------
def _book(exchange, bid, ask, ts=None):
    ts = ts or utcnow()
    return BookSnapshot(
        exchange=exchange, symbol="BTCUSDT", ts=ts,
        bids=[BookLevel(price=bid, size=2.0)], asks=[BookLevel(price=ask, size=2.0)],
    )


def test_walk_book_real_depth():
    # سير 1.5 وحدة: 1.0 @100 ثم 0.5 @101 → متوسط (100×1.0 + 101×0.5)/1.5 = 100.333…
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
    assert opp.classification == "informational"  # عبر منصات = معلوماتي إلا بتمكين صريح


def test_arb_executable_when_pair_enabled():
    """التنفيذ القابل للتفعيل فقط عند تمكين صريح للزوج من المستخدم."""
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
    assert opp is None  # دفتر قديم — لا فرصة


def test_arb_missing_fees_informational():
    scanner = ArbitrageScanner(taker_fees={"a": 0.0004})  # b بلا رسوم معروفة
    opp = scanner.compare("BTC", "USDT", "crypto", 1.0, {
        "a": _book("a", 99.0, 100.0),
        "b": _book("b", 100.5, 101.0),
    }, recent_volatility=0.0)
    assert opp is not None  # الفرصة تُسجَّل معلوماتياً — الرسوم المجهولة ليست رفضاً صامتاً
    assert opp.classification == "informational"
    assert "رسوم" in opp.reason_ar


# ---------- M2: الحيتان ----------
def test_whale_large_trade_detected():
    now = utcnow()
    trades = [Trade(exchange="x", symbol="BTCUSDT", trade_id=str(i), ts=now,
                    price=50000.0, size=0.01, aggressor="buy") for i in range(60)]
    trades.append(Trade(exchange="x", symbol="BTCUSDT", trade_id="big", ts=now,
                        price=50000.0, size=50.0, aggressor="buy"))  # 2.5M USD fixture
    w = WhaleWatcher(min_notional_usd=1_000_000)
    events = w.detect_large_trades(trades)
    assert len(events) == 1 and events[0].notional_usd == pytest.approx(2_500_000)


def test_whale_unfilled_order_not_a_trade():
    """أمر ضخم على سعر جديد (appeared) بلا أي تنفيذ = نية غير مؤكدة، ليس تداولاً."""
    w = WhaleWatcher()
    prev = _book("x", 100.0, 101.0)
    curr = BookSnapshot(exchange="x", symbol="BTCUSDT", ts=utcnow(),
                        bids=[BookLevel(price=100.0, size=2.0)],
                        asks=[BookLevel(price=102.0, size=5000.0)])  # سعر جديد لم يكن موجوداً
    events = w.book_events(prev, curr, big_size=1000.0)
    assert events, "يجب رصد الأمر الضخم الجديد"
    assert all("unconfirmed" in e.label for e in events)  # نية غير مؤكدة — ليس تداولاً
    flags = w.detect_spoof_possible(events, fills_at_prices=set())
    assert any("ممكن" in f for f in flags)  # يوسم "ممكن" فقط


def test_accumulation_from_executed_flow():
    now = utcnow()
    buys = [Trade(exchange="x", symbol="BTCUSDT", trade_id=f"b{i}", ts=now,
                  price=100.0, size=1.0, aggressor="buy") for i in range(40)]
    sells = [Trade(exchange="x", symbol="BTCUSDT", trade_id=f"s{i}", ts=now,
                   price=100.0, size=0.1, aggressor="sell") for i in range(10)]
    w = WhaleWatcher()
    sig = w.accumulation(buys + sells)
    assert sig.direction == "accumulation"


# ---------- M3: اختراق/إدراج ----------
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
    closes[-1] = 103.0; highs[-1] = 103.5; vols[-1] = 50.0  # إغلاق خارج النطاق + حجم
    ev = sc.breakout("x", "TESTUSDT", closes, highs, lows, vols,
                     depth_usd=100_000, spread_pct=0.01)
    assert ev is not None and ev.direction == "up"


def test_wick_alone_is_not_breakout():
    sc = ListingBreakoutScanner()
    n = 120
    closes = np.full(n, 100.0); highs = np.full(n, 101.0); lows = np.full(n, 99.0)
    vols = np.full(n, 10.0)
    highs[-1] = 110.0  # ذيل فقط — الإغلاق داخل النطاق
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
                     cross_exchange_moves={"other": -0.01})  # حركة معاكسة بالمنصات الأخرى!
    assert ev.risk_label == "suspicious"
    assert ("x", "TESTUSDT") in sc.blocklist


# ---------- M5 ----------
def test_adaptive_no_decision_without_min_trades():
    ev = AdaptiveEvaluator(min_trades=30)
    for r in [0.5, -0.5, 0.5]: ev.record("s", r)
    res = ev.evaluate("s")
    assert res["status"] == "insufficient_data"


def test_adaptive_disable_only_with_ci_justification():
    ev = AdaptiveEvaluator(min_trades=30, bootstrap_iters=200)
    rng = np.random.default_rng(3)
    for r in rng.normal(-0.3, 0.2, 60): ev.record("bad", float(r))   # expectancy سالب واثق
    res = ev.evaluate("bad")
    assert res["status"] == "disabled"
    assert res["ci95"][1] < 0  # الحد الأعلى سالب = مبرر إحصائي


def test_walk_forward_sanity():
    assert walk_forward_sanity(0.5, 0.4)
    assert not walk_forward_sanity(0.5, 0.1)


# ---------- M6 ----------
def test_ranking_transparent_and_unavailable_marked():
    ranker = OpportunityRanker()
    ranked = ranker.rank([
        {"kind": "signal", "label": "BTC", "raw_components": {
            "liquidity": 0.9, "spread": 0.8, "regime_fit": 0.7, "volatility_fit": 0.6,
            "strategy_agreement": 0.8, "execution_feasibility": 0.9}},
        {"kind": "whale", "label": "XXX", "raw_components": {
            "liquidity": None, "spread": 0.5, "regime_fit": 0.4, "volatility_fit": 0.5,
            "strategy_agreement": 0.3, "execution_feasibility": None}},  # بيانات حرجة ناقصة
    ])
    assert ranked[0].label == "BTC"
    assert any("غير متوفر" in r for r in ranked[1].unavailable_reasons)


# ---------- بوابة المخاطر ----------
def _req(**kw):
    base = dict(
        exchange="binance_spot", symbol="BTCUSDT", side="buy", direction="long",
        entry=100.0, stop_loss=95.0, take_profit=110.0, risk_reward=2.0,
        instrument=fixture_spec(), account_equity=10_000.0, open_trades=0,
        symbol_exposure_pct=0.0, correlated_exposure_pct=0.0,
        daily_pnl_pct=0.0, weekly_pnl_pct=0.0, current_drawdown_pct=0.0,
        book_slippage_pct=0.01, current_spread_pct=0.02,
        atr_now=1.0, atr_baseline=1.0, tick_rate_per_sec=5.0,
    )
    base.update(kw)
    return OrderRequest(**base)


def test_risk_gate_approves_valid_order_with_exact_size():
    gate = RiskGate(RiskConfig(risk_per_trade_pct=1.0))
    d = gate.check(_req())
    assert d.approved
    assert d.position_size > 0
    # 1% من 10000 = 100 مخاطرة / (100-95)=5 لكل وحدة = 20 وحدة
    assert d.position_size == pytest.approx(20.0)


def test_risk_gate_rejects_bad_rr():
    gate = RiskGate()
    assert not gate.check(_req(risk_reward=1.0)).approved


def test_risk_gate_rejects_missing_stop():
    gate = RiskGate()
    assert not gate.check(_req(stop_loss=0.0)).approved


def test_risk_gate_daily_loss_limit():
    gate = RiskGate()
    d = gate.check(_req(daily_pnl_pct=-3.5))
    assert not d.approved and "اليومية" in d.reason_ar


def test_risk_gate_kill_switch_blocks_everything():
    gate = RiskGate()
    gate.trigger_kill_switch()
    d = gate.check(_req())
    assert not d.approved and "kill switch" in d.reason_ar


def test_risk_gate_news_guard_atr_spike():
    gate = RiskGate()
    d = gate.check(_req(atr_now=5.0, atr_baseline=1.0))
    assert not d.approved and "حراسة" in d.reason_ar


def test_risk_gate_slippage_reject():
    gate = RiskGate()
    assert not gate.check(_req(book_slippage_pct=0.5)).approved


def test_risk_gate_min_notional_zero_size_rejected():
    gate = RiskGate(RiskConfig(risk_per_trade_pct=0.001))
    assert not gate.check(_req()).approved  # حجم تحت الحد الأدنى


# ---------- الطابور ----------
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
    assert b is None  # نفس المهمة لا تكرر


def test_queue_lease_expiry_requeued(tmp_path):
    q = DurableQueue(str(tmp_path / "q.db"))
    q.enqueue("analysis", {"x": 1})
    q.lease_next(lease_seconds=0.01)  # عقد انتهى فوراً
    import time
    time.sleep(0.05)
    job = q.lease_next()
    assert job is not None and job.kind == "analysis"


def test_order_state_machine_valid_transitions():
    q = OrderTracker.__new__(OrderTracker)
    q.orders = {}
    oid = "o1"
    assert q.transition(oid, OrderState.SUBMITTED)
    assert q.transition(oid, OrderState.FILLED)
    assert not q.transition(oid, OrderState.OPEN)  # لا انتقال من filled


def test_reconciliation_exchange_is_truth():
    t = OrderTracker.__new__(OrderTracker)
    t.orders = {"o1": OrderState.PENDING, "o2": OrderState.OPEN}
    fixes = t.reconcile({"o1": OrderState.FILLED, "o2": OrderState.OPEN})
    assert "o1" in fixes and t.orders["o1"] == OrderState.FILLED


# ---------- تعبئة ورقية ----------
def test_paper_fill_from_real_book():
    book = _book("x", 99.9, 100.0)
    model = PaperFillModel(taker_fee_pct=0.1)
    fill = model.simulate_fill(book, "buy", 1.0, "x", "BTCUSDT")
    assert fill.filled_price == pytest.approx(100.0)
    assert fill.fee == pytest.approx(0.1)  # 0.1% من 100
    assert fill.slippage_pct == pytest.approx(0.0)


def test_paper_fill_no_depth_no_fill():
    book = BookSnapshot(exchange="x", symbol="BTCUSDT", ts=utcnow(),
                        bids=[], asks=[])
    model = PaperFillModel()
    assert model.simulate_fill(book, "buy", 1.0, "x", "BTCUSDT") is None


# ---------- backtest ----------
def test_backtester_insufficient_history_says_so():
    closes, highs, lows, vols = fixture_candles(n=100)
    ctx = StrategyContext("x", "BTCUSDT", "crypto", closes, highs, lows, vols,
                          0.02, 100_000)
    bt = Backtester()
    rep = bt.run(TrendPullback(), ctx, RiskGate())
    assert rep.insufficient_history
    assert "لا نتائج تُختلق" in rep.note_ar


def test_backtester_runs_and_reports():
    closes, highs, lows, vols = fixture_candles(n=400)
    ctx = StrategyContext("x", "BTCUSDT", "crypto", closes, highs, lows, vols,
                          0.02, 100_000)
    bt = Backtester()
    rep = bt.run(TrendPullback(), ctx, RiskGate())
    assert not rep.insufficient_history
    assert rep.trades >= 0  # قد لا تطلق الاستراتيجية — لا مشكلة


def test_monte_carlo_outputs():
    bt = Backtester()
    res = bt.monte_carlo(np.array([1.0, -0.5, 1.2, -0.3, 0.8]), iters=200)
    assert res["ok"] and res["final_p50"] != res["final_p95"]


# ---------- فئات الأدوات ----------
def test_instrument_classes_never_mixed():
    from app.connectors.base import classify
    assert classify("PAXG", "USDT", "PAXGUSDT") == InstrumentClass.TOKENIZED_GOLD.value
    assert classify("BTC", "USDT", "BTCUSDT") == InstrumentClass.CRYPTO.value
    assert classify("XAU", "USDT", "XAUUSD_USDT") == InstrumentClass.GOLD_DERIVATIVE.value


def test_no_guaranteed_profit_claims_in_code():
    """سلامة: لا ادعاء ربح مضمون في أي ملف مصدر (بحث UTF-8 ببايثون لا grep)."""
    banned = ("ضمان الربح", "guaranteed profit", "أرباح مضمونة", "ربح مضمون")
    for path in list((REPO_ROOT / "app").rglob("*.py")) + [REPO_ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"عبارة ممنوعة في {path}: {phrase}"