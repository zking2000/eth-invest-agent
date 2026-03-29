from __future__ import annotations

from typing import Any


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    ema_values = [values[0]]
    for value in values[1:]:
        ema_values.append((value - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


def rsi_series(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [50.0 for _ in values]
    gains = [0.0]
    losses = [0.0]
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    avg_gain = sum(gains[1 : period + 1]) / period if len(values) > period else sum(gains[1:]) / max(len(values) - 1, 1)
    avg_loss = sum(losses[1 : period + 1]) / period if len(values) > period else sum(losses[1:]) / max(len(values) - 1, 1)
    rsi_values = [50.0] * len(values)
    start = min(period, len(values) - 1)
    for idx in range(start, len(values)):
        if idx > period:
            avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
            avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period
        if avg_loss == 0:
            rsi_values[idx] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_values[idx] = 100 - (100 / (1 + rs))
    return rsi_values


def atr_series(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    if not highs:
        return []
    true_ranges = []
    for idx in range(len(highs)):
        prev_close = closes[idx - 1] if idx > 0 else closes[idx]
        true_ranges.append(max(highs[idx] - lows[idx], abs(highs[idx] - prev_close), abs(lows[idx] - prev_close)))
    atr_values = [true_ranges[0]]
    for idx in range(1, len(true_ranges)):
        prev_atr = atr_values[-1]
        atr_values.append(((prev_atr * (period - 1)) + true_ranges[idx]) / period)
    return atr_values


def macd_hist_series(values: list[float]) -> list[float]:
    ema_fast = ema_series(values, 12)
    ema_slow = ema_series(values, 26)
    macd_line = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]
    signal_line = ema_series(macd_line, 9)
    return [macd - signal for macd, signal in zip(macd_line, signal_line)]


def enrich_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [item["close"] for item in candles]
    highs = [item["high"] for item in candles]
    lows = [item["low"] for item in candles]
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    rsi14 = rsi_series(closes, 14)
    atr14 = atr_series(highs, lows, closes, 14)
    macd_hist = macd_hist_series(closes)
    enriched: list[dict[str, Any]] = []
    for idx, candle in enumerate(candles):
        payload = dict(candle)
        payload.update(
            {
                "ema20": ema20[idx],
                "ema50": ema50[idx],
                "rsi14": rsi14[idx],
                "atr14": atr14[idx],
                "macd_hist": macd_hist[idx],
            }
        )
        enriched.append(payload)
    return enriched


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fmt_price(value: float) -> str:
    return f"{value:,.2f}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def percent_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return ((b - a) / a) * 100.0


def ema_slope_up(candles: list[dict[str, Any]], key: str, lookback: int) -> bool:
    if len(candles) <= lookback:
        return False
    return candles[-1][key] > candles[-1 - lookback][key]
