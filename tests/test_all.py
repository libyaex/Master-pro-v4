# M5 التكيفي + M6 الترتيب + بوابة المخاطر
import numpy as np
import pytest

from app.models import InstrumentClass, InstrumentSpec
from app.modules.m5_adaptive import AdaptiveEvaluator, walk_forward_sanity
from app.modules.m6_ranking import OpportunityRanker
from app.risk.gate import OrderRequest, RiskConfig, RiskGate


def fixture_spec(symbol="BTCUSDT"):
    return InstrumentSpec(
        exchange="binance_spot", symbol=symbol, base=symbol[:-4], quote="USDT",
        instrument_class=InstrumentClass.CRYPTO, status="TRADING", market_type="spot",
        tick_size=0.01, lot_step=0.0001, min_notional=5.0,
    )


def test_adaptive_no_decision_without_min_trades():
    ev = AdaptiveEvaluator(min_trades=30)
    for r in [0.5, -0.5, 0.5]:
        ev.record("s", r)
    res = ev.evaluate("s")
    assert res["status"] == "insufficient_data"


def test_adaptive_disable_only_with_ci_justification():
    ev = AdaptiveEvaluator(min_trades=30, bootstrap_iters=200)
    rng = np.random.default_rng(3)
    for r in rng.normal(-0.3, 0.2, 60):
        ev.record("bad", float(r))
    res = ev.evaluate("bad")
    assert res["status"] == "disabled"
    assert res["ci95"][1] < 0


def test_walk_forward_sanity():
    assert walk_forward_sanity(0.5, 0.4)
    assert not walk_forward_sanity(0.5, 0.1)


def test_ranking_transparent_and_unavailable_marked():
    ranker = OpportunityRanker()
    ranked = ranker.rank([
        {"kind": "signal", "label": "BTC", "raw_components": {
            "liquidity": 0.9, "spread": 0.8, "regime_fit": 0.7, "volatility_fit": 0.6,
            "strategy_agreement": 0.8, "execution_feasibility": 0.9}},
        {"kind": "whale", "label": "XXX", "raw_components": {
            "liquidity": None, "spread": 0.5, "regime_fit": 0.4, "volatility_fit": 0.5,
            "strategy_agreement": 0.3, "execution_feasibility": None}},
    ])
    assert ranked[0].label == "BTC"
    assert any("غير متوفر" in r for r in ranked[1].unavailable_reasons)


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
    assert not gate.check(_req()).approved
