# اختبارات المؤشرات المحلية وسجل الإشارة (M4)
import math

import numpy as np
import pytest

from app.engine import indicators as ind
from app.models import InstrumentClass
from app.strategies.signal_record import EvidenceItem, SignalRecord


def fixture_candles(n=300, start=50000.0, drift=0.0002, seed=7):
    rng = np.random.default_rng(seed)
    closes = start * np.cumprod(1 + rng.normal(drift, 0.004, n))
    highs = closes * (1 + np.abs(rng.normal(0, 0.002, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.002, n)))
    vols = np.abs(rng.normal(1000, 200, n))
    return closes, highs, lows, vols


def test_ema_matches_manual():
    vals = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    e = ind.ema(vals, 3)
    assert math.isnan(e[1])
    assert e[2] == pytest.approx(2.0)
    assert e[3] == pytest.approx(3.0)


def test_rsi_bounds_and_trend():
    c = np.linspace(100, 200, 50)
    r = ind.rsi(c)
    assert not math.isnan(r[-1]) and r[-1] > 70


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
            tp1=110.0, tp2=120.0, tp3=0.0,
            risk_reward=2.0, confidence=75,
            evidence=[EvidenceItem(feature="t", value="1", explanation_ar="f")],
            invalidation="x", source_modules=["s"],
        )


def test_signal_bad_level_order_rejected():
    with pytest.raises(ValueError):
        _make_signal(entry=100.0, stop=105.0)
