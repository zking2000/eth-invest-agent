from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from eth_agent.features.indicators import clamp, ema_series
from eth_agent.i18n import tr
from eth_agent.utils.io import ensure_parent

try:
    import matplotlib.pyplot as plt  # type: ignore
except Exception:
    plt = None


def build_chart_svg(
    candles: list[dict[str, Any]],
    current_price: float,
    output_path: Path,
    title: str,
    locale: str = "en",
) -> Path | None:
    if len(candles) < 2:
        return None
    width = 720
    height = 360
    padding = 32
    highs = [float(item["high"]) for item in candles]
    lows = [float(item["low"]) for item in candles]
    closes = [float(item["close"]) for item in candles]
    ema20 = ema_series(closes, 20)
    min_price = min(lows)
    max_price = max(highs)
    span = max(max_price - min_price, 1e-6)

    def price_to_y(price: float) -> float:
        return height - padding - ((price - min_price) / span) * (height - padding * 2)

    candle_width = max((width - padding * 2) / max(len(candles), 1) * 0.55, 2.0)
    candle_parts: list[str] = []
    ema_points: list[str] = []
    for idx, candle in enumerate(candles):
        x = padding + ((idx + 0.5) / max(len(candles), 1)) * (width - padding * 2)
        open_y = price_to_y(float(candle["open"]))
        close_y = price_to_y(float(candle["close"]))
        high_y = price_to_y(float(candle["high"]))
        low_y = price_to_y(float(candle["low"]))
        top = min(open_y, close_y)
        body_h = max(abs(close_y - open_y), 1.5)
        color = "#16a34a" if candle["close"] >= candle["open"] else "#dc2626"
        candle_parts.append(
            f'<line x1="{x:.1f}" y1="{high_y:.1f}" x2="{x:.1f}" y2="{low_y:.1f}" stroke="{color}" stroke-width="1.5"/>'
        )
        candle_parts.append(
            f'<rect x="{x - candle_width / 2:.1f}" y="{top:.1f}" width="{candle_width:.1f}" height="{body_h:.1f}" fill="{color}" rx="1"/>'
        )
        ema_points.append(f"{x:.1f},{price_to_y(float(ema20[idx])):.1f}")

    current_y = clamp(price_to_y(current_price), padding, height - padding)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="100%" height="100%" fill="#0f172a"/>'
        f'<text x="{padding}" y="24" fill="#e2e8f0" font-size="18" font-family="Helvetica, Arial, sans-serif">{title}</text>'
        f'<text x="{padding}" y="{height - 10}" fill="#94a3b8" font-size="12" font-family="Helvetica, Arial, sans-serif">{tr(locale, f"最近 {len(candles)} 根 15m K线 + EMA20", f"Last {len(candles)} 15m candles + EMA20")}</text>'
        f'<line x1="{padding}" y1="{current_y:.1f}" x2="{width - padding}" y2="{current_y:.1f}" stroke="#475569" stroke-dasharray="4 4"/>'
        f'<text x="{width - padding}" y="{current_y - 6:.1f}" text-anchor="end" fill="#e2e8f0" font-size="12" font-family="Helvetica, Arial, sans-serif">${current_price:,.2f}</text>'
        f'{"".join(candle_parts)}'
        f'<polyline fill="none" stroke="#38bdf8" stroke-width="2" points="{" ".join(ema_points)}"/>'
        "</svg>"
    )
    ensure_parent(output_path)
    output_path.write_text(svg)
    return output_path


def plot_backtest_results(equity_curve: pd.Series, output_path: Path, title: str = "Backtest Equity Curve") -> Path:
    if plt is None:
        raise RuntimeError("matplotlib is not installed.")
    ensure_parent(output_path)
    plt.figure(figsize=(10, 4))
    equity_curve.plot(title=title)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    return output_path


def plot_price_with_signals(frame: pd.DataFrame, output_path: Path, signal_column: str = "predicted_signal") -> Path:
    if plt is None:
        raise RuntimeError("matplotlib is not installed.")
    ensure_parent(output_path)
    plt.figure(figsize=(12, 5))
    plt.plot(frame["timestamp"], frame["close"], label="close")
    buys = frame[frame[signal_column] > 0]
    sells = frame[frame[signal_column] < 0]
    plt.scatter(buys["timestamp"], buys["close"], label="buy", marker="^")
    plt.scatter(sells["timestamp"], sells["close"], label="sell", marker="v")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    return output_path


def plot_monthly_returns(monthly_returns: list[dict[str, Any]], output_path: Path, title: str = "Monthly Returns") -> Path:
    if plt is None:
        raise RuntimeError("matplotlib is not installed.")
    ensure_parent(output_path)
    frame = pd.DataFrame(monthly_returns)
    plt.figure(figsize=(10, 4))
    if not frame.empty:
        colors = ["#16a34a" if value >= 0 else "#dc2626" for value in frame["return_pct"]]
        plt.bar(frame["month"], frame["return_pct"], color=colors)
        plt.xticks(rotation=30, ha="right")
    plt.title(title)
    plt.ylabel("Return %")
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    return output_path


def plot_parameter_sweep_heatmap(
    frame: pd.DataFrame,
    output_path: Path,
    *,
    value_column: str,
    title: str,
    xlabel: str,
    ylabel: str,
) -> Path:
    if plt is None:
        raise RuntimeError("matplotlib is not installed.")
    ensure_parent(output_path)
    heatmap = frame.copy()
    plt.figure(figsize=(8, 6))
    image = plt.imshow(heatmap.values, aspect="auto", cmap="viridis")
    plt.colorbar(image, label=value_column)
    plt.xticks(range(len(heatmap.columns)), [str(value) for value in heatmap.columns], rotation=30, ha="right")
    plt.yticks(range(len(heatmap.index)), [str(value) for value in heatmap.index])
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    return output_path
