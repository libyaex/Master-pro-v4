"""محرك المؤشرات المحلي — لا خدمات خارجية، لا AI مدفوعة.

كل الحسابات على شموع مغلقة فقط (no repainting).
"""
from __future__ import annotations

import math

import numpy as np


def ema(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) < period:
        return np.full_like(values, np.nan)
    k = 2 / (period + 1)
    out = np.empty_like(values)
    out[: period - 1] = np.nan
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    if len(values) < period + 1:
        return np.full_like(values, np.nan)
    delta = np.diff(values, prepend=values[0])
    gains = np.clip(delta, 0, None)
    losses = np.clip(-delta, 0, None)
    avg_gain = np.mean(gains[1 : period + 1])
    avg_loss = np.mean(losses[1 : period + 1])
    out = np.empty_like(values)
    out[:period] = np.nan
    for i in range(period, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - 100 / (1 + rs)
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    if len(close) < period + 1:
        return np.full_like(close, np.nan)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty_like(close)
    out[:period] = np.nan
    out[period] = tr[1 : period + 1].mean()
    for i in range(period + 1, len(close)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    if n < 2 * period:
        return np.full_like(close, np.nan)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    up_move = high - np.roll(high, 1)
    dn_move = np.roll(low, 1) - low
    plus_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    plus_di = 100 * np.convolve(plus_dm, np.ones(period) / period, mode="valid") / (np.convolve(tr, np.ones(period) / period, mode="valid") + 1e-12)
    minus_di = 100 * np.convolve(minus_dm, np.ones(period) / period, mode="valid") / (np.convolve(tr, np.ones(period) / period, mode="valid") + 1e-12)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)
    adx_vals = np.convolve(dx, np.ones(period) / period, mode="valid")
    out = np.full(n, np.nan)
    out[2 * period - 1 :] = adx_vals[: n - (2 * period - 1)]
    return out


def bollinger(values: np.ndarray, period: int = 20, ndev: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(values) < period:
        n = len(values)
        return np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    mid = np.convolve(values, np.ones(period) / period, mode="valid")
    pad = np.full(period - 1, np.nan)
    mid = np.concatenate([pad, mid])
    std = np.array([np.nanstd(values[max(0, i - period + 1) : i + 1]) for i in range(len(values))])
    upper = mid + ndev * std
    lower = mid - ndev * std
    return mid, upper, lower


def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    tp = (high + low + close) / 3
    cum_v = np.cumsum(volume)
    out = np.where(cum_v > 0, np.cumsum(tp * volume) / np.maximum(cum_v, 1e-12), close)
    return out


def zscore(values: np.ndarray, window: int = 100) -> np.ndarray:
    """z-score متحرك — لكشف حجم/تقلب غير طبيعي."""
    n = len(values)
    out = np.full(n, np.nan)
    if n < window:
        return out
    for i in range(window - 1, n):
        w = values[i - window + 1 : i + 1]
        s = np.std(w)
        out[i] = 0.0 if s < 1e-12 else (values[i] - np.mean(w)) / s
    return out


def percentile_rank(values: np.ndarray, value: float, window: int = 100) -> float:
    if len(values) < window:
        return float("nan")
    w = values[-window:]
    return float((w < value).sum() / len(w) * 100)


def swing_levels(high: np.ndarray, low: np.ndarray, lookback: int = 3) -> tuple[float, float]:
    """آخر قمة/قاع Swing — لبنية السوق (BOS/CHoCH)."""
    if len(high) < lookback * 2 + 1:
        return float("nan"), float("nan")
    swing_high = float(np.max(high[-lookback * 2 :]))
    swing_low = float(np.min(low[-lookback * 2 :]))
    return swing_high, swing_low


def market_regime(close: np.ndarray, adx_vals: np.ndarray, atr_vals: np.ndarray, atr_pct_threshold: float = 2.0) -> str:
    """كاشف النظام: trending | ranging | high_volatility — يبوّب الاستراتيجيات."""
    if len(close) < 2 or math.isnan(close[-1]):
        return "unknown"
    a = adx_vals[-1]
    atr_ratio = (atr_vals[-1] / close[-1] * 100) if not math.isnan(atr_vals[-1]) else 0.0
    if atr_ratio >= atr_pct_threshold:
        return "high_volatility"
    if not math.isnan(a) and a >= 25:
        return "trending"
    return "ranging"