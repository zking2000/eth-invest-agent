from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from eth_agent.features.indicators import atr_series, ema_series, macd_hist_series, rsi_series
from eth_agent.utils.io import ensure_parent


@dataclass
class FeatureConfig:
    target_horizon: int = 4
    target_threshold_pct: float = 0.35


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window).mean()
    std = series.rolling(window).std().replace(0, np.nan)
    return (series - mean) / std


def build_feature_frame(frame: pd.DataFrame, config: FeatureConfig | None = None) -> pd.DataFrame:
    cfg = config or FeatureConfig()
    df = frame.copy().reset_index(drop=True)
    close = df["close"].astype(float).tolist()
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()

    df["return_1"] = df["close"].pct_change()
    df["return_4"] = df["close"].pct_change(4)
    df["return_16"] = df["close"].pct_change(16)
    df["volume_change_1"] = df["volume"].pct_change()

    df["ema20"] = ema_series(close, 20)
    df["ema50"] = ema_series(close, 50)
    df["rsi14"] = rsi_series(close, 14)
    df["atr14"] = atr_series(high, low, close, 14)
    df["macd_hist"] = macd_hist_series(close)

    df["ema_spread"] = (df["ema20"] - df["ema50"]) / df["close"].replace(0, np.nan)
    df["price_vs_ema20"] = (df["close"] - df["ema20"]) / df["close"].replace(0, np.nan)
    df["price_vs_ema50"] = (df["close"] - df["ema50"]) / df["close"].replace(0, np.nan)
    df["volatility_12"] = df["return_1"].rolling(12).std()
    df["volatility_48"] = df["return_1"].rolling(48).std()
    df["volume_zscore_24"] = _rolling_zscore(df["volume"], 24)
    df["close_zscore_24"] = _rolling_zscore(df["close"], 24)
    df["breakout_20"] = (df["close"] > df["high"].shift(1).rolling(20).max()).astype(int)
    df["pullback_to_ema20"] = ((df["close"] - df["ema20"]).abs() / df["atr14"].replace(0, np.nan)).fillna(99.0)

    future_return = (df["close"].shift(-cfg.target_horizon) - df["close"]) / df["close"] * 100.0
    df["future_return_pct"] = future_return
    df["target_up"] = (future_return >= cfg.target_threshold_pct).astype(int)
    df["target_down"] = (future_return <= -cfg.target_threshold_pct).astype(int)
    df["target_signal"] = 0
    df.loc[df["target_up"] == 1, "target_signal"] = 1
    df.loc[df["target_down"] == 1, "target_signal"] = -1
    return df.replace([np.inf, -np.inf], np.nan)


def save_feature_frame(frame: pd.DataFrame, output_path: Path) -> None:
    ensure_parent(output_path)
    frame.to_csv(output_path, index=False)


def load_feature_frame(path: Path) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0)
    parse_dates = ["timestamp"] if "timestamp" in header.columns else None
    return pd.read_csv(path, parse_dates=parse_dates)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    excluded = {"timestamp", "target_up", "target_down", "target_signal", "target_class", "future_return_pct"}
    return [column for column in frame.columns if column not in excluded]
