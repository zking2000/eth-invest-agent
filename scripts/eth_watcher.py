#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any


BINANCE_BASE_URLS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
]


def resolve_project_dir() -> Path:
    env_path = os.environ.get("ETH_AGENT_HOME")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


PROJECT_DIR = resolve_project_dir()
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.json"
DEFAULT_STATE_PATH = PROJECT_DIR / "state" / "runtime.json"


def resolve_project_path(path_value: str | Path, *, base_dir: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir or PROJECT_DIR) / path


def resolve_openclaw_bin() -> str:
    env_value = os.environ.get("OPENCLAW_BIN")
    if env_value:
        return env_value
    discovered = shutil.which("openclaw")
    if discovered:
        return discovered
    return "/opt/homebrew/bin/openclaw"


def build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    path_parts: list[str] = []
    for part in ["/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")]:
        if not part:
            continue
        for entry in str(part).split(":"):
            if entry and entry not in path_parts:
                path_parts.append(entry)
    env["PATH"] = ":".join(path_parts)
    return env


def infer_default_target() -> str | None:
    candidates = [
        Path.home() / ".clawdbot" / "clawdbot.json",
        Path.home() / ".openclaw" / "openclaw.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            allow_from = data.get("channels", {}).get("imessage", {}).get("allowFrom", [])
            if allow_from:
                return str(allow_from[0])
        except Exception:
            continue
    return None


DEFAULT_CONFIG: dict[str, Any] = {
    "symbol": "ETHUSDT",
    "strategy_profile": "balanced",
    "runtime": {
        "poll_interval_seconds": 60,
        "http_timeout_seconds": 12,
        "kline_limit": 320,
    },
    "notification": {
        "enabled": True,
        "channel": "imessage",
        "target": infer_default_target(),
        "reply_language": "en",
        "cooldown_minutes": 30,
        "min_score_to_alert": 68,
        "max_alerts_per_day": 6,
        "min_price_move_percent_for_repeat": 1.2,
        "quiet_hours": {
            "enabled": True,
            "start_hour": 23,
            "end_hour": 8,
            "override_for_buy_trigger": True,
        },
        "chart": {
            "enabled": True,
            "bars": 48,
            "path": "state/latest-chart.svg",
        },
        "active_windows": {
            "enabled": True,
            "windows": ["08:00-12:00", "20:00-02:00"],
            "override_for_buy_trigger": True,
        },
        "followup_tracking": {
            "enabled": True,
            "duration_minutes": 30,
            "interval_minutes": 10,
            "min_move_percent": 0.6,
            "only_when_position_open": False,
        },
        "daily_summary": {
            "enabled": True,
            "send_times": ["09:00"],
            "attach_chart": True,
            "llm_enabled": True,
            "llm_timeout_seconds": 120,
            "openclaw_agent_id": "eth-daily-summary",
            "thinking": "off",
        },
    },
    "rules": {
        "near_buy_score": 68,
        "buy_trigger_score": 84,
        "pullback_max_atr_distance": 0.75,
        "pullback_rsi_min": 44,
        "pullback_rsi_max": 63,
        "breakout_volume_multiple": 1.4,
        "reclaim_rsi_min": 48,
        "reversal_rsi_floor": 38,
        "reversal_volume_multiple": 1.15,
        "max_extension_atr": 1.4,
        "stop_atr_multiplier": 1.3,
        "breakout_lookback_bars": 20,
        "ema_slope_lookback": 3,
        "position_size_hint": "8%-12%",
        "take_profit_rr_1": 1.5,
        "take_profit_rr_2": 2.3,
    },
    "profiles": {
        "scalp": {
            "runtime": {
                "poll_interval_seconds": 45,
            },
            "notification": {
                "cooldown_minutes": 20,
                "min_score_to_alert": 64,
                "max_alerts_per_day": 10,
                "min_price_move_percent_for_repeat": 0.8,
            },
            "rules": {
                "near_buy_score": 64,
                "buy_trigger_score": 78,
                "pullback_max_atr_distance": 0.95,
                "breakout_volume_multiple": 1.25,
                "max_extension_atr": 1.65,
                "stop_atr_multiplier": 1.05,
                "position_size_hint": "6%-10%",
            },
        },
        "balanced": {
            "runtime": {
                "poll_interval_seconds": 60,
            },
            "notification": {
                "cooldown_minutes": 30,
                "min_score_to_alert": 68,
                "max_alerts_per_day": 6,
            },
            "rules": {
                "near_buy_score": 68,
                "buy_trigger_score": 84,
                "pullback_max_atr_distance": 0.75,
                "breakout_volume_multiple": 1.4,
                "stop_atr_multiplier": 1.3,
                "position_size_hint": "8%-12%",
            },
        },
        "swing": {
            "runtime": {
                "poll_interval_seconds": 90,
            },
            "notification": {
                "cooldown_minutes": 60,
                "min_score_to_alert": 72,
                "max_alerts_per_day": 4,
                "min_price_move_percent_for_repeat": 1.8,
            },
            "rules": {
                "near_buy_score": 72,
                "buy_trigger_score": 88,
                "pullback_max_atr_distance": 0.55,
                "breakout_volume_multiple": 1.55,
                "max_extension_atr": 1.15,
                "stop_atr_multiplier": 1.5,
                "position_size_hint": "10%-15%",
            },
        },
    },
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_today() -> str:
    return datetime.now().date().isoformat()


def local_hour() -> int:
    return datetime.now().hour


def local_minute_of_day() -> int:
    now = datetime.now()
    return now.hour * 60 + now.minute


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text())


def save_json_file(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def apply_strategy_profile(config: dict[str, Any]) -> dict[str, Any]:
    profile_name = str(config.get("strategy_profile", "balanced"))
    profile = config.get("profiles", {}).get(profile_name, {})
    merged = deep_merge(config, profile)
    merged["strategy_profile"] = profile_name
    return merged


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        save_json_file(path, DEFAULT_CONFIG)
    config = deep_merge(DEFAULT_CONFIG, load_json_file(path, DEFAULT_CONFIG))
    config = apply_strategy_profile(config)
    if not config.get("notification", {}).get("target"):
        config["notification"]["enabled"] = False
    return config


def load_state(path: Path) -> dict[str, Any]:
    return ensure_state_defaults(
        load_json_file(
            path,
            {
                "last_sent": {},
                "last_analysis": {},
                "alert_history": [],
                "tracking": {},
                "position": {
                    "active": False,
                    "entry_price": None,
                    "size_hint": "",
                    "opened_at": None,
                    "notes": "",
                },
                "chat": {
                    "processed_message_ids": [],
                },
                "daily_summary": {
                    "sent_keys": [],
                    "last_llm_usage": {},
                },
            },
        )
    )


def ensure_state_defaults(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("last_sent", {})
    state.setdefault("last_analysis", {})
    state.setdefault("alert_history", [])
    state.setdefault("tracking", {})
    state.setdefault(
        "position",
        {
            "active": False,
            "entry_price": None,
            "size_hint": "",
            "opened_at": None,
            "notes": "",
        },
    )
    state.setdefault(
        "chat",
        {
            "processed_message_ids": [],
        },
    )
    state.setdefault(
        "daily_summary",
        {
            "sent_keys": [],
            "last_llm_usage": {},
        },
    )
    chat = state["chat"]
    processed_ids = chat.get("processed_message_ids", [])
    if not isinstance(processed_ids, list):
        processed_ids = []
    chat["processed_message_ids"] = [str(item) for item in processed_ids[-40:] if str(item)]
    daily_summary = state["daily_summary"]
    sent_keys = daily_summary.get("sent_keys", [])
    if not isinstance(sent_keys, list):
        sent_keys = []
    daily_summary["sent_keys"] = [str(item) for item in sent_keys[-30:] if str(item)]
    if not isinstance(daily_summary.get("last_llm_usage"), dict):
        daily_summary["last_llm_usage"] = {}
    return state


def fetch_json(base_path: str, params: dict[str, Any], timeout_seconds: int) -> Any:
    query = urllib.parse.urlencode(params)
    last_error: Exception | None = None
    for base_url in BINANCE_BASE_URLS:
        url = f"{base_url}{base_path}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "openclaw-eth-watcher/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"market data fetch failed: {last_error}")


def fetch_price(symbol: str, timeout_seconds: int) -> float:
    payload = fetch_json("/api/v3/ticker/price", {"symbol": symbol}, timeout_seconds)
    return float(payload["price"])


def fetch_klines(symbol: str, interval: str, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    raw = fetch_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
        timeout_seconds,
    )
    candles: list[dict[str, Any]] = []
    for row in raw:
        candles.append(
            {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]),
            }
        )
    return candles


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def rsi_series(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [50.0 for _ in values]
    gains = [0.0]
    losses = [0.0]
    for prev, current in zip(values[:-1], values[1:]):
        delta = current - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    rsis = [50.0 for _ in values]
    avg_gain = sum(gains[1 : period + 1]) / period if len(gains) > period else 0.0
    avg_loss = sum(losses[1 : period + 1]) / period if len(losses) > period else 0.0
    for idx in range(period, len(values)):
        if idx > period:
            avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
            avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period
        if avg_loss == 0:
            rsis[idx] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsis[idx] = 100.0 - (100.0 / (1.0 + rs))
    return rsis


def atr_series(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    if not closes:
        return []
    tr_values = [highs[0] - lows[0]]
    for idx in range(1, len(closes)):
        tr_values.append(
            max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1]),
            )
        )
    atr_values = [tr_values[0]]
    for idx in range(1, len(tr_values)):
        if idx < period:
            atr_values.append(sum(tr_values[: idx + 1]) / (idx + 1))
        else:
            atr_values.append(((atr_values[-1] * (period - 1)) + tr_values[idx]) / period)
    return atr_values


def macd_hist_series(values: list[float]) -> list[float]:
    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal_line = ema_series(macd_line, 9)
    return [a - b for a, b in zip(macd_line, signal_line)]


def enrich_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    rsi14 = rsi_series(closes, 14)
    atr14 = atr_series(highs, lows, closes, 14)
    macd_hist = macd_hist_series(closes)
    enriched: list[dict[str, Any]] = []
    for idx, candle in enumerate(candles):
        item = dict(candle)
        item["ema20"] = ema20[idx]
        item["ema50"] = ema50[idx]
        item["rsi14"] = rsi14[idx]
        item["atr14"] = atr14[idx]
        item["macd_hist"] = macd_hist[idx]
        enriched.append(item)
    return enriched


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fmt_price(value: float) -> str:
    return f"{value:,.2f}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_hhmm(value: str) -> int:
    hour_str, minute_str = value.split(":", 1)
    return int(hour_str) * 60 + int(minute_str)


def percent_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return ((b - a) / a) * 100.0


def ema_slope_up(candles: list[dict[str, Any]], key: str, lookback: int) -> bool:
    if len(candles) <= lookback:
        return False
    return candles[-1][key] > candles[-1 - lookback][key]


def add_reason(reasons: list[str], condition: bool, text: str) -> None:
    if condition:
        reasons.append(text)


def build_signal_payload(
    *,
    name: str,
    score: int,
    reasons: list[str],
    stop_loss: float,
    entry_hint: str,
    entry_zone: str,
    entry_reference: float,
    position_size_hint: str,
    entry_kind: str = "reference",
    entry_low: float | None = None,
    entry_high: float | None = None,
    entry_trigger: float | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "score": score,
        "reasons": reasons[:6],
        "stop_loss": round(stop_loss, 2),
        "entry_hint": entry_hint,
        "entry_zone": entry_zone,
        "entry_reference": round(entry_reference, 2),
        "position_size_hint": position_size_hint,
        "entry_kind": entry_kind,
    }
    if entry_low is not None:
        payload["entry_low"] = round(entry_low, 2)
    if entry_high is not None:
        payload["entry_high"] = round(entry_high, 2)
    if entry_trigger is not None:
        payload["entry_trigger"] = round(entry_trigger, 2)
    return payload


def build_take_profit(entry_reference: float, stop_loss: float, rules: dict[str, Any]) -> dict[str, float]:
    risk = max(entry_reference - stop_loss, 0.01)
    tp1 = entry_reference + risk * float(rules["take_profit_rr_1"])
    tp2 = entry_reference + risk * float(rules["take_profit_rr_2"])
    return {
        "entry_reference": round(entry_reference, 2),
        "risk_per_unit": round(risk, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr_to_tp1": round((tp1 - entry_reference) / risk, 2),
        "rr_to_tp2": round((tp2 - entry_reference) / risk, 2),
    }


def analyze_market(config: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    symbol = config["symbol"]
    runtime = config["runtime"]
    timeout_seconds = int(runtime["http_timeout_seconds"])
    limit = int(runtime["kline_limit"])

    candles_5m = enrich_candles(fetch_klines(symbol, "5m", limit, timeout_seconds))
    candles_15m = enrich_candles(fetch_klines(symbol, "15m", limit, timeout_seconds))
    candles_1h = enrich_candles(fetch_klines(symbol, "1h", limit, timeout_seconds))
    candles_4h = enrich_candles(fetch_klines(symbol, "4h", limit, timeout_seconds))
    current_price = fetch_price(symbol, timeout_seconds)

    closed_5m = candles_5m[:-1]
    closed_15m = candles_15m[:-1]
    closed_1h = candles_1h[:-1]
    closed_4h = candles_4h[:-1]

    if min(len(closed_5m), len(closed_15m), len(closed_1h), len(closed_4h)) < 80:
        raise RuntimeError("not enough candle history for signal calculation")

    fast = closed_5m[-1]
    fast_prev = closed_5m[-2]
    confirm = closed_15m[-1]
    confirm_prev = closed_15m[-2]
    macro = closed_1h[-1]
    regime = closed_4h[-1]

    rules = config["rules"]
    eps = 1e-9
    lookback = int(rules["breakout_lookback_bars"])
    slope_lookback = int(rules["ema_slope_lookback"])
    breakout_window = closed_15m[-(lookback + 1) : -1]
    breakout_level = max(item["high"] for item in breakout_window)
    average_volume_15m = average([item["volume"] for item in breakout_window])
    average_volume_5m = average([item["volume"] for item in closed_5m[-21:-1]])

    regime_bull = regime["ema20"] > regime["ema50"]
    regime_slope = ema_slope_up(closed_4h, "ema20", slope_lookback)
    macro_bull = macro["ema20"] > macro["ema50"]
    macro_slope = ema_slope_up(closed_1h, "ema20", slope_lookback)
    confirm_bull = confirm["ema20"] > confirm["ema50"]
    fast_bull = fast["ema20"] > fast["ema50"]
    not_overextended = ((current_price - confirm["ema20"]) / max(confirm["atr14"], eps)) <= rules["max_extension_atr"]
    macd_improving = confirm["macd_hist"] > confirm_prev["macd_hist"]
    trend_alignment = sum([regime_bull, macro_bull, confirm_bull, fast_bull])

    trend_score = 0
    trend_reasons: list[str] = []
    add_reason(trend_reasons, regime_bull, "4h EMA20 > EMA50，长趋势偏多")
    add_reason(trend_reasons, regime_slope, "4h EMA20 继续抬升")
    add_reason(trend_reasons, macro_bull, "1h EMA20 > EMA50，中期趋势配合")
    add_reason(trend_reasons, macro_slope, "1h EMA20 保持上拐")
    add_reason(trend_reasons, confirm_bull, "15m EMA20 > EMA50，执行周期偏多")
    add_reason(trend_reasons, fast_bull, "5m 均线站上，短线不弱")
    add_reason(trend_reasons, not_overextended, "价格未明显远离 15m EMA20，追高风险可控")
    add_reason(trend_reasons, macd_improving, "15m MACD 柱状图增强")

    if regime_bull:
        trend_score += 18
    if regime_slope:
        trend_score += 6
    if macro_bull:
        trend_score += 16
    if macro_slope:
        trend_score += 5
    if confirm_bull:
        trend_score += 13
    if fast_bull:
        trend_score += 5
    if not_overextended:
        trend_score += 5
    if macd_improving:
        trend_score += 4

    pullback_distance = abs(current_price - fast["ema20"]) / max(fast["atr14"], eps)
    in_pullback_zone = pullback_distance <= rules["pullback_max_atr_distance"]
    rsi_rebound = rules["pullback_rsi_min"] <= fast["rsi14"] <= rules["pullback_rsi_max"] and fast["rsi14"] > fast_prev["rsi14"]
    reclaim_fast_ema = fast_prev["close"] < fast_prev["ema20"] and fast["close"] > fast["ema20"]
    breakout = current_price > breakout_level and confirm["close"] > confirm["ema20"]
    volume_spike = confirm["volume"] >= average_volume_15m * rules["breakout_volume_multiple"]
    reversal_ready = (
        fast_prev["rsi14"] <= rules["reversal_rsi_floor"]
        and fast["rsi14"] >= rules["reclaim_rsi_min"]
        and fast["close"] > fast_prev["high"]
        and fast["volume"] >= average_volume_5m * rules["reversal_volume_multiple"]
    )

    atr_for_stop = max(confirm["atr14"], fast["atr14"], eps)
    signal_scores: list[dict[str, Any]] = []

    pullback_score = trend_score
    pullback_reasons = list(trend_reasons)
    if in_pullback_zone:
        pullback_score += 14
        pullback_reasons.append("价格回到 5m EMA20 附近，接近趋势回踩带")
    if rsi_rebound:
        pullback_score += 10
        pullback_reasons.append("5m RSI 回升，回踩后动能恢复")
    if reclaim_fast_ema:
        pullback_score += 8
        pullback_reasons.append("5m 重新站回 EMA20，上车位置更清晰")
    if trend_alignment < 3:
        pullback_score = min(pullback_score, 57)
        pullback_reasons.append("趋势共振不足，回踩信号降级处理")
    signal_scores.append(
        build_signal_payload(
            name="pullback",
            score=pullback_score,
            reasons=pullback_reasons,
            stop_loss=min(fast["ema20"], confirm["ema20"]) - atr_for_stop * rules["stop_atr_multiplier"],
            entry_hint=f"{fmt_price(fast['ema20'])}-{fmt_price(confirm['ema20'])} 区间分批试仓",
            entry_zone=f"${fmt_price(min(fast['ema20'], confirm['ema20']))}-${fmt_price(max(fast['ema20'], confirm['ema20']))}",
            entry_reference=(fast["ema20"] + confirm["ema20"]) / 2.0,
            position_size_hint=rules["position_size_hint"],
            entry_kind="range",
            entry_low=min(fast["ema20"], confirm["ema20"]),
            entry_high=max(fast["ema20"], confirm["ema20"]),
        )
    )

    breakout_score = trend_score
    breakout_reasons = list(trend_reasons)
    if breakout:
        breakout_score += 16
        breakout_reasons.append(f"价格突破近 {lookback} 根 15m 高点")
    if volume_spike:
        breakout_score += 12
        breakout_reasons.append("15m 成交量明显放大，突破更可信")
    if confirm["close"] > confirm_prev["high"]:
        breakout_score += 6
        breakout_reasons.append("15m 收盘继续推高，没有假突破迹象")
    if not regime_bull and not macro_bull:
        breakout_score = min(breakout_score, 60)
        breakout_reasons.append("更大级别趋势未翻多，突破只按观察级别处理")
    signal_scores.append(
        build_signal_payload(
            name="breakout",
            score=breakout_score,
            reasons=breakout_reasons,
            stop_loss=breakout_level - atr_for_stop * rules["stop_atr_multiplier"],
            entry_hint=f"{fmt_price(breakout_level)} 上方站稳后再跟进，避免假突破",
            entry_zone=f">${fmt_price(breakout_level)}",
            entry_reference=breakout_level,
            position_size_hint=rules["position_size_hint"],
            entry_kind="above",
            entry_trigger=breakout_level,
        )
    )

    reversal_score = trend_score // 2
    reversal_reasons: list[str] = []
    add_reason(reversal_reasons, reversal_ready, "5m 出现超跌后的快速收复与放量反弹")
    add_reason(reversal_reasons, fast["close"] > fast["ema20"], "价格重新站回短线均线")
    add_reason(reversal_reasons, fast["rsi14"] > fast_prev["rsi14"], "RSI 拐头向上")
    if reversal_ready:
        reversal_score += 28
    if fast["close"] > fast["ema20"]:
        reversal_score += 8
    if fast["rsi14"] > fast_prev["rsi14"]:
        reversal_score += 6
    if trend_alignment < 2:
        reversal_score = min(reversal_score, 62)
        reversal_reasons.append("只适合小仓位抢反弹，不能当趋势主升浪")
    signal_scores.append(
        build_signal_payload(
            name="reversal",
            score=reversal_score,
            reasons=reversal_reasons,
            stop_loss=min(fast["low"], fast_prev["low"]) - atr_for_stop * 0.9,
            entry_hint=f"若继续站稳 {fmt_price(fast['ema20'])} 上方，可轻仓试反弹",
            entry_zone=f"${fmt_price(fast['ema20'])} 上方确认",
            entry_reference=fast["ema20"],
            position_size_hint="4%-8%",
            entry_kind="above",
            entry_trigger=fast["ema20"],
        )
    )

    best_signal = max(signal_scores, key=lambda item: item["score"])
    label = "watch"
    if best_signal["score"] >= rules["buy_trigger_score"]:
        label = "buy_trigger"
    elif best_signal["score"] >= rules["near_buy_score"]:
        label = "near_buy"

    if best_signal["name"] == "pullback" and not (in_pullback_zone and (rsi_rebound or reclaim_fast_ema)):
        label = "watch"
    if best_signal["name"] == "breakout" and not breakout:
        label = "watch"
    if best_signal["name"] == "reversal" and not reversal_ready:
        label = "watch"

    signal_name_map = {
        "watch": "观望",
        "pullback": "趋势回踩买点",
        "breakout": "放量突破买点",
        "reversal": "超跌反弹买点",
    }
    regime_map = {
        4: "强多头",
        3: "偏多",
        2: "震荡",
        1: "偏弱",
        0: "弱势",
    }

    analysis = {
        "symbol": symbol,
        "generated_at": utc_now_iso(),
        "strategy_profile": config["strategy_profile"],
        "price": current_price,
        "score": best_signal["score"],
        "label": label,
        "primary_signal": best_signal["name"] if label != "watch" else "watch",
        "signal_name": signal_name_map[best_signal["name"]] if label != "watch" else signal_name_map["watch"],
        "entry_hint": best_signal["entry_hint"] if label != "watch" else "等待更清晰买点",
        "entry_zone": best_signal["entry_zone"] if label != "watch" else "等待信号确认",
        "entry_reference": best_signal["entry_reference"],
        "entry_plan": {
            "kind": best_signal.get("entry_kind", "reference"),
            "low": best_signal.get("entry_low"),
            "high": best_signal.get("entry_high"),
            "trigger": best_signal.get("entry_trigger"),
            "reference": best_signal["entry_reference"],
        },
        "stop_loss": best_signal["stop_loss"],
        "position_size_hint": best_signal["position_size_hint"],
        "signal_key": f"{label}:{best_signal['name']}:{confirm['close_time']}",
        "market_regime": regime_map.get(trend_alignment, "震荡"),
        "signals": {item["name"]: item for item in signal_scores},
        "take_profit": (
            build_take_profit(best_signal["entry_reference"], best_signal["stop_loss"], rules)
            if label != "watch"
            else build_take_profit(current_price, best_signal["stop_loss"], rules)
        ),
        "metrics": {
            "5m_rsi14": round(fast["rsi14"], 2),
            "5m_ema20": round(fast["ema20"], 2),
            "15m_ema20": round(confirm["ema20"], 2),
            "15m_ema50": round(confirm["ema50"], 2),
            "15m_breakout_level": round(breakout_level, 2),
            "15m_volume_ratio": round(confirm["volume"] / max(average_volume_15m, eps), 2),
            "1h_ema20": round(macro["ema20"], 2),
            "1h_ema50": round(macro["ema50"], 2),
            "4h_ema20": round(regime["ema20"], 2),
            "4h_ema50": round(regime["ema50"], 2),
            "price_vs_15m_ema20_pct": round(percent_change(confirm["ema20"], current_price), 2),
        },
        "conditions": {
            "regime_bull": regime_bull,
            "regime_slope": regime_slope,
            "macro_bull": macro_bull,
            "macro_slope": macro_slope,
            "confirm_bull": confirm_bull,
            "fast_bull": fast_bull,
            "in_pullback_zone": in_pullback_zone,
            "rsi_rebound": rsi_rebound,
            "reclaim_fast_ema": reclaim_fast_ema,
            "breakout": breakout,
            "volume_spike": volume_spike,
            "reversal_ready": reversal_ready,
            "not_overextended": not_overextended,
            "macd_improving": macd_improving,
        },
        "reasons": best_signal["reasons"][:6] if label != "watch" else trend_reasons[:4] or ["当前结构仍偏震荡，继续等待"],
    }
    state = state or {}
    analysis["position_active"] = position_is_open(state)
    analysis["position_entry_price"] = position_entry_reference(state, analysis["entry_reference"])
    return analysis


def build_chart_svg(candles: list[dict[str, Any]], current_price: float, output_path: Path, title: str, locale: str = "en") -> Path | None:
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

    trend_up = closes[-1] >= closes[0]
    current_y = price_to_y(current_price)
    current_y = clamp(current_y, padding, height - padding)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#0f172a"/>
<text x="{padding}" y="24" fill="#e2e8f0" font-size="18" font-family="Helvetica, Arial, sans-serif">{title}</text>
<text x="{padding}" y="{height - 10}" fill="#94a3b8" font-size="12" font-family="Helvetica, Arial, sans-serif">{tr(locale, f"最近 {len(candles)} 根 15m K线 + EMA20", f"Last {len(candles)} 15m candles + EMA20")}</text>
<line x1="{padding}" y1="{current_y:.1f}" x2="{width - padding}" y2="{current_y:.1f}" stroke="#475569" stroke-dasharray="4 4"/>
{''.join(candle_parts)}
<polyline fill="none" stroke="#38bdf8" stroke-width="2" points="{' '.join(ema_points)}"/>
<text x="{width - padding}" y="{padding}" text-anchor="end" fill="#e2e8f0" font-size="14" font-family="Helvetica, Arial, sans-serif">${fmt_price(max_price)}</text>
<text x="{width - padding}" y="{height - 18}" text-anchor="end" fill="#94a3b8" font-size="14" font-family="Helvetica, Arial, sans-serif">${fmt_price(min_price)}</text>
<text x="{width - padding}" y="{current_y - 6:.1f}" text-anchor="end" fill="#38bdf8" font-size="13" font-family="Helvetica, Arial, sans-serif">{tr(locale, f"现价 ${fmt_price(current_price)}", f"Price ${fmt_price(current_price)}")}</text>
<text x="{padding}" y="{padding}" fill="{('#16a34a' if trend_up else '#dc2626')}" font-size="13" font-family="Helvetica, Arial, sans-serif">{tr(locale, "上涨结构" if trend_up else "回落结构", "Rising structure" if trend_up else "Pullback structure")}</text>
</svg>
"""
    ensure_parent(output_path)
    output_path.write_text(svg)
    return output_path


def should_attach_chart(config: dict[str, Any]) -> bool:
    return bool(config.get("notification", {}).get("chart", {}).get("enabled", False))


def in_active_windows(config: dict[str, Any]) -> bool:
    active = config.get("notification", {}).get("active_windows", {})
    if not active.get("enabled", False):
        return True
    windows = active.get("windows", [])
    if not windows:
        return True
    now_minute = local_minute_of_day()
    for window in windows:
        try:
            start_text, end_text = str(window).split("-", 1)
            start = parse_hhmm(start_text)
            end = parse_hhmm(end_text)
        except Exception:
            continue
        if start == end:
            return True
        if start < end and start <= now_minute < end:
            return True
        if start > end and (now_minute >= start or now_minute < end):
            return True
    return False


def in_quiet_hours(config: dict[str, Any]) -> bool:
    quiet = config.get("notification", {}).get("quiet_hours", {})
    if not quiet.get("enabled", False):
        return False
    start = int(quiet.get("start_hour", 23))
    end = int(quiet.get("end_hour", 8))
    hour = local_hour()
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def get_reply_language(config: dict[str, Any]) -> str:
    value = str(config.get("notification", {}).get("reply_language", "en")).strip().lower()
    return "zh" if value.startswith("zh") else "en"


def tr(locale: str, zh: str, en: str) -> str:
    return zh if locale == "zh" else en


def strength_label(score: int, locale: str = "en") -> str:
    if score >= 90:
        return tr(locale, "A+ 强", "A+ Strong")
    if score >= 84:
        return tr(locale, "A 强", "A Strong")
    if score >= 76:
        return tr(locale, "B+ 较强", "B+ Fairly Strong")
    if score >= 68:
        return tr(locale, "B 观察偏多", "B Constructive Watch")
    if score >= 55:
        return tr(locale, "C 中性", "C Neutral")
    return tr(locale, "D 观望", "D Wait")


def localize_signal_name(signal: str, locale: str, *, detailed: bool = True) -> str:
    names = {
        "watch": {"zh": "观望", "en": "Watch"},
        "pullback": {"zh": "趋势回踩买点" if detailed else "趋势回踩", "en": "Trend Pullback Setup" if detailed else "Trend Pullback"},
        "breakout": {"zh": "放量突破买点" if detailed else "放量突破", "en": "Volume Breakout Setup" if detailed else "Volume Breakout"},
        "reversal": {"zh": "超跌反弹买点" if detailed else "超跌反弹", "en": "Oversold Reversal Setup" if detailed else "Oversold Reversal"},
    }
    payload = names.get(signal, {"zh": signal, "en": signal})
    return payload["zh" if locale == "zh" else "en"]


def localize_market_regime(regime: str, locale: str) -> str:
    mapping = {
        "强多头": "Strong Bullish",
        "偏多": "Bullish Bias",
        "震荡": "Range",
        "偏弱": "Weak Bias",
        "弱势": "Bearish",
    }
    if locale == "zh":
        return regime
    return mapping.get(regime, regime)


def localize_reason(reason: str, locale: str) -> str:
    if locale == "zh":
        return reason
    mapping = {
        "4h EMA20 > EMA50，长趋势偏多": "4h EMA20 is above EMA50, so the higher-timeframe trend is bullish.",
        "4h EMA20 继续抬升": "4h EMA20 is still rising.",
        "1h EMA20 > EMA50，中期趋势配合": "1h EMA20 is above EMA50, so the medium-term trend agrees.",
        "1h EMA20 保持上拐": "1h EMA20 keeps sloping upward.",
        "15m EMA20 > EMA50，执行周期偏多": "15m EMA20 is above EMA50, so the execution timeframe stays constructive.",
        "5m 均线站上，短线不弱": "5m moving averages are holding, so short-term structure is not weak.",
        "价格未明显远离 15m EMA20，追高风险可控": "Price is not stretched far above the 15m EMA20, so chasing risk is still manageable.",
        "15m MACD 柱状图增强": "15m MACD histogram is improving.",
        "价格回到 5m EMA20 附近，接近趋势回踩带": "Price has returned near the 5m EMA20, close to the pullback buy zone.",
        "5m RSI 回升，回踩后动能恢复": "5m RSI is rebounding, showing momentum recovery after the pullback.",
        "5m 重新站回 EMA20，上车位置更清晰": "5m price has reclaimed EMA20, making the entry cleaner.",
        "趋势共振不足，回踩信号降级处理": "Trend alignment is weak, so the pullback setup is downgraded.",
        "价格突破近 20 根 15m 高点": "Price has broken above the recent 20-bar 15m high.",
        "15m 成交量明显放大，突破更可信": "15m volume expanded clearly, making the breakout more credible.",
        "15m 收盘继续推高，没有假突破迹象": "15m closes are still pushing higher, with no clear fake breakout signal.",
        "更大级别趋势未翻多，突破只按观察级别处理": "Higher timeframes are not bullish yet, so this breakout is still only a watch setup.",
        "5m 出现超跌后的快速收复与放量反弹": "5m shows a fast reclaim after an oversold move, with supportive volume.",
        "价格重新站回短线均线": "Price has reclaimed the short-term moving average.",
        "RSI 拐头向上": "RSI has turned upward.",
        "只适合小仓位抢反弹，不能当趋势主升浪": "This only suits a small rebound trade, not a main trend continuation entry.",
        "当前结构仍偏震荡，继续等待": "The current structure is still choppy, so waiting is better.",
        "1h 与 15m 均线同向，趋势配合": "1h and 15m moving averages are aligned, so the trend is cooperating.",
        "价格回到 EMA20 附近，追高风险较低": "Price is back near EMA20, so chasing risk is lower.",
        "RSI 回升，短线动能在恢复": "RSI is recovering, showing improving short-term momentum.",
    }
    return mapping.get(reason, reason)


def localize_reasons(reasons: list[str], locale: str) -> list[str]:
    return [localize_reason(reason, locale) for reason in reasons]


def build_entry_zone_display(analysis: dict[str, Any], locale: str) -> str:
    if analysis["label"] == "watch":
        return tr(locale, "等待信号确认", "Wait for confirmation")
    plan = analysis.get("entry_plan", {})
    kind = str(plan.get("kind", "reference"))
    if kind == "range":
        low = float(plan.get("low") or analysis["entry_reference"])
        high = float(plan.get("high") or analysis["entry_reference"])
        return f"${fmt_price(low)}-${fmt_price(high)}"
    if kind == "above":
        trigger = float(plan.get("trigger") or analysis["entry_reference"])
        return f">${fmt_price(trigger)}"
    return f"${fmt_price(float(analysis['entry_reference']))}"


def build_entry_hint_display(analysis: dict[str, Any], locale: str) -> str:
    plan = analysis.get("entry_plan", {})
    trigger = float(plan.get("trigger") or analysis["entry_reference"])
    if analysis["label"] == "watch":
        return tr(locale, "等待更清晰买点", "Wait for a cleaner setup")
    if analysis["primary_signal"] == "pullback":
        return tr(locale, "回到入场区后分批试仓", "Scale in inside the entry zone")
    if analysis["primary_signal"] == "breakout":
        return tr(
            locale,
            f"站稳 {fmt_price(trigger)} 上方后再跟进，避免假突破",
            f"Wait for price to hold above {fmt_price(trigger)} before following",
        )
    if analysis["primary_signal"] == "reversal":
        return tr(
            locale,
            f"若继续站稳 {fmt_price(trigger)} 上方，可轻仓试反弹",
            f"Probe only if price holds above {fmt_price(trigger)}",
        )
    return tr(locale, "等待更清晰买点", "Wait for a cleaner setup")


def normalize_chat_text(text: str) -> str:
    cleaned = str(text or "").strip().lower()
    for old, new in [
        ("？", "?"),
        ("，", ","),
        ("。", "."),
        ("：", ":"),
        ("；", ";"),
        ("\n", " "),
        ("\t", " "),
    ]:
        cleaned = cleaned.replace(old, new)
    return " ".join(cleaned.split())


def normalize_chat_text(text: str) -> str:
    cleaned = str(text or "").strip().lower()
    for old, new in [
        ("？", "?"),
        ("，", ","),
        ("。", "."),
        ("：", ":"),
        ("；", ";"),
        ("\n", " "),
        ("\t", " "),
    ]:
        cleaned = cleaned.replace(old, new)
    return " ".join(cleaned.split())


def extract_chat_intents(text: str) -> set[str]:
    normalized = normalize_chat_text(text)
    compact = normalized.replace(" ", "")
    intents: set[str] = set()

    def has_any(tokens: list[str]) -> bool:
        return any(token in normalized or token in compact for token in tokens)

    if not normalized:
        return intents
    if has_any(["help", "帮助", "菜单", "指令", "commands"]):
        return {"help"}

    wants_status = has_any(
        [
            "eth",
            "以太",
            "现在能买吗",
            "能买",
            "能买吗",
            "买点",
            "买入点",
            "是否买入",
            "判断",
            "分析",
            "怎么看",
            "can i buy now",
            "buy now",
            "buy setup",
            "entry setup",
            "what do you think",
        ]
    )
    if wants_status:
        intents.add("status")
    if has_any(["距离", "多远", "离买点", "离入场", "距离买点", "距离入场", "还差", "distance", "how far", "far from entry", "far from buy"]):
        intents.add("distance")
    if has_any(["少量", "小仓", "轻仓", "先买", "先上", "试仓", "少买", "small", "small size", "small position", "start small", "probe"]):
        intents.add("small_position")
    if has_any(["表现", "时长", "多久", "跟踪", "浮盈", "浮亏", "涨跌", "表现如何", "performance", "pnl", "doing", "how is it doing", "tracking"]):
        intents.add("performance")
    if has_any(["持仓", "仓位", "position", "status"]):
        intents.add("position")
    if has_any(["为什么", "理由", "依据", "原因", "why", "reason", "because"]):
        intents.add("reasons")

    if intents and "status" not in intents:
        intents.add("status")
    return intents


def format_pct(value: float) -> str:
    return f"{value:+.2f}%"


def is_chat_message_processed(state: dict[str, Any], message_id: str | None) -> bool:
    if not message_id:
        return False
    processed = state.get("chat", {}).get("processed_message_ids", [])
    return str(message_id) in {str(item) for item in processed}


def mark_chat_message_processed(state: dict[str, Any], message_id: str | None) -> None:
    if not message_id:
        return
    chat = state.setdefault("chat", {})
    processed = [str(item) for item in chat.get("processed_message_ids", []) if str(item)]
    processed.append(str(message_id))
    chat["processed_message_ids"] = processed[-40:]


def build_entry_distance_summary(analysis: dict[str, Any], locale: str) -> str:
    if analysis["label"] == "watch":
        return tr(locale, "距离买点: 当前还没有执行级入场区", "Distance to entry: there is no execution-grade entry zone yet.")
    plan = analysis.get("entry_plan", {})
    price = float(analysis["price"])
    kind = str(plan.get("kind", "reference"))
    if kind == "range":
        low = float(plan.get("low") or analysis["entry_reference"])
        high = float(plan.get("high") or analysis["entry_reference"])
        if low <= price <= high:
            return tr(locale, f"距离买点: 已进入建议入场区 {build_entry_zone_display(analysis, locale)}", f"Distance to entry: price is already inside the suggested entry zone {build_entry_zone_display(analysis, locale)}.")
        if price > high:
            gap = abs(percent_change(high, price))
            return tr(locale, f"距离买点: 现价高于入场区上沿 {gap:.2f}%，更适合等回踩", f"Distance to entry: price is {gap:.2f}% above the top of the entry zone, so waiting for a pullback is cleaner.")
        gap = abs(percent_change(price, low))
        return tr(locale, f"距离买点: 现价低于入场区下沿 {gap:.2f}%，还没回到理想承接带", f"Distance to entry: price is still {gap:.2f}% below the lower edge of the entry zone.")
    if kind == "above":
        trigger = float(plan.get("trigger") or analysis["entry_reference"])
        if price >= trigger:
            gap = abs(percent_change(trigger, price))
            return tr(locale, f"距离买点: 已站上触发位 {gap:.2f}%", f"Distance to entry: price is already {gap:.2f}% above the trigger.")
        gap = abs(percent_change(price, trigger))
        return tr(locale, f"距离买点: 距离触发位还差 {gap:.2f}%", f"Distance to entry: price is still {gap:.2f}% below the trigger.")
    gap = abs(percent_change(price, float(analysis["entry_reference"])))
    return tr(locale, f"距离买点: 距离参考入场位约 {gap:.2f}%", f"Distance to entry: price is about {gap:.2f}% away from the reference entry.")


def build_performance_summary(analysis: dict[str, Any], state: dict[str, Any], locale: str) -> str:
    tracking = state.get("tracking", {})
    now_ts = time.time()
    if position_is_open(state):
        entry_price = position_entry_reference(state, analysis["entry_reference"])
        pnl_pct = percent_change(entry_price, analysis["price"])
        return tr(locale, f"当前表现: 相对你的开仓价 {format_pct(pnl_pct)}", f"Current performance: {format_pct(pnl_pct)} versus your recorded entry.")
    if tracking.get("active"):
        entry_reference = float(tracking.get("entry_reference", analysis["entry_reference"]))
        pnl_pct = percent_change(entry_reference, analysis["price"])
        elapsed_minutes = max(int((now_ts - float(tracking.get("started_ts", now_ts))) // 60), 0)
        return tr(locale, f"当前表现: 最近信号跟踪 {elapsed_minutes} 分钟，现价相对参考位 {format_pct(pnl_pct)}", f"Current performance: {format_pct(pnl_pct)} versus the tracked signal reference over the last {elapsed_minutes} minutes.")
    last_sent = state.get("last_sent", {})
    if last_sent.get("price"):
        anchor_price = float(last_sent["price"])
        pnl_pct = percent_change(anchor_price, analysis["price"])
        elapsed_minutes = max(int((now_ts - float(last_sent.get("ts", now_ts))) // 60), 0)
        return tr(locale, f"当前表现: 距离上次提醒 {elapsed_minutes} 分钟，价格变动 {format_pct(pnl_pct)}", f"Current performance: price has moved {format_pct(pnl_pct)} since the last alert {elapsed_minutes} minutes ago.")
    pnl_pct = percent_change(float(analysis["entry_reference"]), analysis["price"])
    return tr(locale, f"当前表现: 相对当前信号参考位 {format_pct(pnl_pct)}", f"Current performance: {format_pct(pnl_pct)} versus the current signal reference.")


def build_small_position_advice(analysis: dict[str, Any], locale: str) -> str:
    label = str(analysis["label"])
    signal = str(analysis["primary_signal"])
    if label == "buy_trigger":
        return tr(
            locale,
            f"少量先买: 可以，按 {analysis['position_size_hint']} 分批更稳，失效位看 ${fmt_price(analysis['stop_loss'])}",
            f"Small starter size: yes. Scaling in around {analysis['position_size_hint']} is cleaner, with invalidation near ${fmt_price(analysis['stop_loss'])}.",
        )
    if label == "near_buy":
        if signal == "pullback":
            return tr(
                locale,
                f"少量先买: 可以轻仓试仓，优先分两笔，仓位先按建议仓位的一半起步 ({analysis['position_size_hint']})",
                f"Small starter size: yes, but keep it light and preferably split into two entries. Start with roughly half of the suggested size ({analysis['position_size_hint']}).",
            )
        if signal == "reversal":
            return tr(locale, f"少量先买: 只能更小仓抢反弹，建议不超过 {analysis['position_size_hint']}", f"Small starter size: only if you treat it as a rebound trade, and keep size no larger than {analysis['position_size_hint']}.")
        return tr(locale, "少量先买: 可以等突破站稳后再小仓，不建议在触发前追价", "Small starter size: better to wait until the breakout actually holds, rather than pre-chasing.")
    return tr(locale, "少量先买: 暂不建议，当前仍以等待更清晰买点为主", "Small starter size: not preferred yet. Waiting for a cleaner setup is better.")


def build_position_summary(state: dict[str, Any], locale: str) -> str:
    position = state.get("position", {})
    if not position.get("active"):
        return tr(locale, "持仓状态: 当前未记录持仓", "Position status: no open position is currently recorded.")
    size_hint = str(position.get("size_hint") or "").strip()
    size_text = tr(locale, f" / 仓位 {size_hint}", f" / size {size_hint}") if size_hint else ""
    return tr(locale, f"持仓状态: 已开仓 @ ${fmt_price(float(position['entry_price']))}{size_text}", f"Position status: open @ ${fmt_price(float(position['entry_price']))}{size_text}")


def build_chat_help_message(locale: str) -> str:
    return "\n".join(
        (
            [
                "【ETH问答】可用提问",
                "1. ETH / 现在能买吗",
                "2. 距离买点多远",
                "3. 可以少量先买入吗",
                "4. 当前表现如何",
                "5. 持仓状态",
                "6. 为什么这样判断",
            ]
            if locale == "zh"
            else [
                "[ETH Assistant] Supported prompts",
                "1. ETH / Can I buy now?",
                "2. How far is price from the entry?",
                "3. Can I start with a small size?",
                "4. How is it performing now?",
                "5. Position status",
                "6. Why this view?",
            ]
        )
    )


def build_chat_reply(query: str, analysis: dict[str, Any], state: dict[str, Any], locale: str) -> str | None:
    intents = extract_chat_intents(query)
    if not intents:
        return None
    if intents == {"help"}:
        return build_chat_help_message(locale)

    lines = [tr(locale, "【ETH问答】", "[ETH Assistant]")]
    if "status" in intents:
        verdict = {
            "buy_trigger": tr(locale, "当前判断: 买点已触发", "Current view: buy setup triggered."),
            "near_buy": tr(locale, "当前判断: 接近买点", "Current view: close to a buy setup."),
            "watch": tr(locale, "当前判断: 还不是执行级买点", "Current view: not an execution-grade buy setup yet."),
        }.get(analysis["label"], tr(locale, "当前判断: 继续观察", "Current view: keep watching."))
        lines.extend(
            [
                verdict,
                tr(locale, f"现价: ${fmt_price(analysis['price'])}", f"Price: ${fmt_price(analysis['price'])}"),
                tr(
                    locale,
                    f"信号: {localize_signal_name(analysis['primary_signal'], locale)} / {localize_market_regime(analysis['market_regime'], locale)}",
                    f"Signal: {localize_signal_name(analysis['primary_signal'], locale)} / {localize_market_regime(analysis['market_regime'], locale)}",
                ),
                tr(locale, f"强度: {strength_label(int(analysis['score']), locale)} ({analysis['score']}/100)", f"Strength: {strength_label(int(analysis['score']), locale)} ({analysis['score']}/100)"),
                tr(locale, f"入场区: {build_entry_zone_display(analysis, locale)}", f"Entry zone: {build_entry_zone_display(analysis, locale)}"),
            ]
        )

    if "distance" in intents or intents == {"status"}:
        lines.append(build_entry_distance_summary(analysis, locale))
    if "performance" in intents:
        lines.append(build_performance_summary(analysis, state, locale))
    if "small_position" in intents or intents == {"status"}:
        lines.append(build_small_position_advice(analysis, locale))
    if "position" in intents:
        lines.append(build_position_summary(state, locale))
    if "reasons" in intents:
        for reason in localize_reasons(analysis["reasons"][:3], locale):
            lines.append(f"- {reason}")
    lines.append(tr(locale, "回复 HELP 可查看指令。", "Reply HELP to see supported prompts."))
    return "\n".join(lines)


def build_followup_message(analysis: dict[str, Any], tracking: dict[str, Any], locale: str) -> str:
    entry_reference = float(tracking.get("entry_reference", analysis.get("entry_reference", analysis["price"])))
    pnl_pct = percent_change(entry_reference, analysis["price"])
    distance_to_tp1 = percent_change(analysis["price"], analysis["take_profit"]["tp1"])
    distance_to_stop = percent_change(analysis["price"], analysis["stop_loss"])
    dollars_to_tp1 = analysis["take_profit"]["tp1"] - analysis["price"]
    dollars_to_stop = analysis["price"] - analysis["stop_loss"]
    direction = tr(locale, "走强", "up") if pnl_pct >= 0 else tr(locale, "回撤", "pulling back")
    lines = [
        tr(locale, "【ETH跟踪】触发后复盘", "[ETH Follow-up] Post-trigger update"),
        tr(locale, f"现价: ${fmt_price(analysis['price'])}", f"Price: ${fmt_price(analysis['price'])}"),
        tr(locale, f"原始信号: {localize_signal_name(analysis['primary_signal'], locale)}", f"Original signal: {localize_signal_name(analysis['primary_signal'], locale)}"),
        tr(locale, f"入场参考: ${fmt_price(entry_reference)}", f"Reference entry: ${fmt_price(entry_reference)}"),
        tr(locale, f"当前表现: {direction} {pnl_pct:+.2f}%", f"Current performance: {direction} {pnl_pct:+.2f}%"),
        tr(locale, f"止盈1距离: {distance_to_tp1:+.2f}% / ${fmt_price(dollars_to_tp1)}", f"Distance to TP1: {distance_to_tp1:+.2f}% / ${fmt_price(dollars_to_tp1)}"),
        tr(locale, f"止损距离: {distance_to_stop:+.2f}% / ${fmt_price(dollars_to_stop)}", f"Distance to stop: {distance_to_stop:+.2f}% / ${fmt_price(dollars_to_stop)}"),
        tr(locale, f"当前结构: {localize_market_regime(analysis['market_regime'], locale)} / {strength_label(int(analysis['score']), locale)}", f"Current structure: {localize_market_regime(analysis['market_regime'], locale)} / {strength_label(int(analysis['score']), locale)}"),
    ]
    for reason in localize_reasons(analysis["reasons"][:2], locale):
        lines.append(f"- {reason}")
    lines.append(tr(locale, "仅供参考，不构成投资建议。", "For reference only. Not financial advice."))
    return "\n".join(lines)


def build_message(analysis: dict[str, Any], locale: str = "en") -> str:
    title_map = {
        "buy_trigger": tr(locale, "【ETH提醒】买点触发", "[ETH Alert] Buy Trigger"),
        "near_buy": tr(locale, "【ETH提醒】接近买点", "[ETH Alert] Near Buy"),
        "watch": tr(locale, "【ETH提醒】观察更新", "[ETH Alert] Watch Update"),
    }
    lines = [
        title_map[analysis["label"]],
        tr(locale, f"现价: ${fmt_price(analysis['price'])}", f"Price: ${fmt_price(analysis['price'])}"),
        tr(locale, f"信号: {localize_signal_name(analysis['primary_signal'], locale)}", f"Signal: {localize_signal_name(analysis['primary_signal'], locale)}"),
        tr(locale, f"类型: {localize_signal_name(analysis['primary_signal'], locale, detailed=False)}", f"Type: {localize_signal_name(analysis['primary_signal'], locale, detailed=False)}"),
        tr(locale, f"风格: {analysis['strategy_profile']}", f"Profile: {analysis['strategy_profile']}"),
        tr(locale, f"市场状态: {localize_market_regime(analysis['market_regime'], locale)}", f"Market regime: {localize_market_regime(analysis['market_regime'], locale)}"),
        tr(locale, f"强度: {strength_label(int(analysis['score']), locale)} ({analysis['score']}/100)", f"Strength: {strength_label(int(analysis['score']), locale)} ({analysis['score']}/100)"),
        tr(locale, f"入场区间: {build_entry_zone_display(analysis, locale)}", f"Entry zone: {build_entry_zone_display(analysis, locale)}"),
        tr(locale, f"操作: {build_entry_hint_display(analysis, locale)}", f"Plan: {build_entry_hint_display(analysis, locale)}"),
        tr(locale, f"仓位: {analysis['position_size_hint']}", f"Size hint: {analysis['position_size_hint']}"),
        tr(locale, f"止损位: ${fmt_price(analysis['stop_loss'])}", f"Stop: ${fmt_price(analysis['stop_loss'])}"),
        tr(locale, f"止盈1: ${fmt_price(analysis['take_profit']['tp1'])}", f"Take profit 1: ${fmt_price(analysis['take_profit']['tp1'])}"),
        tr(locale, f"止盈2: ${fmt_price(analysis['take_profit']['tp2'])}", f"Take profit 2: ${fmt_price(analysis['take_profit']['tp2'])}"),
        tr(locale, f"盈亏比: 1:{analysis['take_profit']['rr_to_tp1']} / 1:{analysis['take_profit']['rr_to_tp2']}", f"Risk/reward: 1:{analysis['take_profit']['rr_to_tp1']} / 1:{analysis['take_profit']['rr_to_tp2']}"),
    ]
    if analysis.get("position_active"):
        lines.append(tr(locale, f"持仓参考: 已开仓 @ ${fmt_price(analysis['position_entry_price'])}", f"Position reference: open @ ${fmt_price(analysis['position_entry_price'])}"))
    for reason in localize_reasons(analysis["reasons"][:3], locale):
        lines.append(f"- {reason}")
    lines.append(tr(locale, "仅供参考，不构成投资建议。", "For reference only. Not financial advice."))
    return "\n".join(lines)


def infer_forecast_bias(analysis: dict[str, Any]) -> tuple[str, str]:
    regime = str(analysis.get("market_regime", "Range"))
    label = str(analysis.get("label", "watch"))
    signal = str(analysis.get("primary_signal", "watch"))
    score = int(analysis.get("score", 0))
    conditions = analysis.get("conditions", {})
    bullish_count = sum(
        [
            bool(conditions.get("regime_bull")),
            bool(conditions.get("macro_bull")),
            bool(conditions.get("confirm_bull")),
            bool(conditions.get("fast_bull")),
        ]
    )
    if label == "buy_trigger" and score >= 84:
        return "bullish", "high"
    if label == "near_buy" and bullish_count >= 2:
        return "bullish", "medium"
    if signal == "breakout" and bool(conditions.get("breakout")):
        return "bullish", "medium"
    if regime in {"强多头", "偏多"}:
        return "bullish", "medium"
    if regime in {"偏弱", "弱势"}:
        return "bearish", "medium"
    return "range", "low"


def build_key_levels(analysis: dict[str, Any]) -> dict[str, float]:
    metrics = analysis.get("metrics", {})
    support = min(
        float(analysis["stop_loss"]),
        float(metrics.get("15m_ema20", analysis["stop_loss"])),
        float(metrics.get("5m_ema20", analysis["stop_loss"])),
    )
    resistance = max(
        float(metrics.get("15m_breakout_level", analysis["price"])),
        float(analysis["take_profit"]["tp1"]),
    )
    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
    }


def build_daily_summary_payload(analysis: dict[str, Any], state: dict[str, Any], locale: str) -> dict[str, Any]:
    bias, confidence = infer_forecast_bias(analysis)
    levels = build_key_levels(analysis)
    return {
        "locale": locale,
        "symbol": analysis["symbol"],
        "generated_at": analysis["generated_at"],
        "price": round(float(analysis["price"]), 2),
        "label": analysis["label"],
        "signal": analysis["primary_signal"],
        "signal_name": localize_signal_name(analysis["primary_signal"], locale),
        "market_regime": localize_market_regime(analysis["market_regime"], locale),
        "score": int(analysis["score"]),
        "entry_zone": build_entry_zone_display(analysis, locale),
        "entry_hint": build_entry_hint_display(analysis, locale),
        "position_size_hint": analysis["position_size_hint"],
        "stop_loss": round(float(analysis["stop_loss"]), 2),
        "take_profit_1": round(float(analysis["take_profit"]["tp1"]), 2),
        "take_profit_2": round(float(analysis["take_profit"]["tp2"]), 2),
        "forecast_bias": bias,
        "forecast_confidence": confidence,
        "support": levels["support"],
        "resistance": levels["resistance"],
        "position_active": bool(analysis.get("position_active")),
        "position_entry_price": round(float(analysis.get("position_entry_price", analysis["entry_reference"])), 2),
        "reasons": localize_reasons(analysis.get("reasons", [])[:4], locale),
        "metrics": analysis.get("metrics", {}),
        "last_alert_date": state.get("last_sent", {}).get("date"),
    }


def build_daily_summary_prompt(payload: dict[str, Any], locale: str) -> str:
    language_text = "Simplified Chinese" if locale == "zh" else "English"
    return "\n".join(
        [
            "You are an ETH market review assistant writing a daily brief for an active trader.",
            "Use ONLY the structured data below.",
            "Do not use tools, do not browse, and do not mention missing data or missing context.",
            f"Write in {language_text}.",
            "Return plain text only, no markdown fences.",
            "Keep it under 700 characters.",
            "Be concrete, not generic. Mention exact levels and a clear action bias.",
            "Must include:",
            "1. A one-line market stance.",
            "2. A next-24h forecast with confidence.",
            "3. Key support and resistance.",
            "4. Whether to act now, wait, or only probe small.",
            "5. One main risk to watch.",
            "",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def build_local_daily_summary(analysis: dict[str, Any], state: dict[str, Any], locale: str) -> str:
    bias, confidence = infer_forecast_bias(analysis)
    levels = build_key_levels(analysis)
    bias_text = {
        "bullish": tr(locale, "偏多", "bullish bias"),
        "bearish": tr(locale, "偏弱", "bearish bias"),
        "range": tr(locale, "震荡", "range-bound"),
    }[bias]
    confidence_text = {
        "high": tr(locale, "高", "high"),
        "medium": tr(locale, "中", "medium"),
        "low": tr(locale, "低", "low"),
    }[confidence]
    action_text = build_small_position_advice(analysis, locale)
    lines = [
        tr(locale, "【ETH日报】市场评价与预测", "[ETH Daily] Market Review and Forecast"),
        tr(
            locale,
            f"市场评价: 当前结构 {localize_market_regime(analysis['market_regime'], locale)}，主信号为 {localize_signal_name(analysis['primary_signal'], locale)}，强度 {strength_label(int(analysis['score']), locale)}。",
            f"Market view: {localize_market_regime(analysis['market_regime'], locale)} structure, primary setup is {localize_signal_name(analysis['primary_signal'], locale)}, strength {strength_label(int(analysis['score']), locale)}.",
        ),
        tr(
            locale,
            f"市场预测: 未来 24 小时更偏 {bias_text}，信心 {confidence_text}。",
            f"Forecast: the next 24 hours lean {bias_text} with {confidence_text} confidence.",
        ),
        tr(
            locale,
            f"关键位: 支撑约 ${fmt_price(levels['support'])}，压力约 ${fmt_price(levels['resistance'])}。",
            f"Key levels: support near ${fmt_price(levels['support'])}, resistance near ${fmt_price(levels['resistance'])}.",
        ),
        tr(
            locale,
            f"当前结论: {build_entry_distance_summary(analysis, locale)}",
            f"Current stance: {build_entry_distance_summary(analysis, locale)}",
        ),
        action_text,
        tr(
            locale,
            f"风险点: {localize_reasons(analysis['reasons'][:1], locale)[0] if analysis['reasons'] else tr(locale, '继续等待更清晰结构。', 'wait for a cleaner structure.')}",
            f"Main risk: {localize_reasons(analysis['reasons'][:1], locale)[0] if analysis['reasons'] else 'wait for a cleaner structure.'}",
        ),
        tr(locale, "仅供参考，不构成投资建议。", "For reference only. Not financial advice."),
    ]
    if position_is_open(state):
        lines.insert(
            4,
            tr(
                locale,
                f"持仓参考: 你的记录开仓价为 ${fmt_price(position_entry_reference(state, analysis['entry_reference']))}。",
                f"Position reference: your recorded entry is ${fmt_price(position_entry_reference(state, analysis['entry_reference']))}.",
            ),
        )
    return "\n".join(lines)


def extract_openclaw_agent_text(payload: dict[str, Any]) -> str:
    result = payload.get("result", {})
    payloads = result.get("payloads", [])
    texts: list[str] = []
    for item in payloads:
        text = str(item.get("text") or "").strip()
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def call_openclaw_llm_summary(prompt: str, config: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    summary_cfg = config.get("notification", {}).get("daily_summary", {})
    command = [
        resolve_openclaw_bin(),
        "agent",
        "--agent",
        str(summary_cfg.get("openclaw_agent_id", "main")),
        "--message",
        prompt,
        "--json",
        "--thinking",
        str(summary_cfg.get("thinking", "minimal")),
        "--timeout",
        str(int(summary_cfg.get("llm_timeout_seconds", 120))),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=int(summary_cfg.get("llm_timeout_seconds", 120)) + 30,
        env=build_subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "openclaw agent call failed")
    payload = json.loads(result.stdout)
    text = extract_openclaw_agent_text(payload)
    usage = payload.get("result", {}).get("meta", {}).get("agentMeta", {}).get("lastCallUsage", {}) or payload.get("result", {}).get("meta", {}).get("agentMeta", {}).get("usage", {}) or {}
    if not text:
        raise RuntimeError("llm returned empty summary")
    return text, usage


def should_send_daily_summary(config: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str]:
    notification = config.get("notification", {})
    daily_cfg = notification.get("daily_summary", {})
    if not notification.get("enabled", False):
        return False, "notification disabled"
    if not daily_cfg.get("enabled", False):
        return False, "daily summary disabled"
    if not notification.get("target"):
        return False, "missing target"
    send_times = daily_cfg.get("send_times", [])
    if not isinstance(send_times, list) or not send_times:
        return False, "no send times"
    now_minute = local_minute_of_day()
    sent_keys = {str(item) for item in state.get("daily_summary", {}).get("sent_keys", [])}
    today = local_today()
    for send_time in send_times:
        try:
            minute_value = parse_hhmm(str(send_time))
        except Exception:
            continue
        send_key = f"{today}:{send_time}"
        if now_minute >= minute_value and send_key not in sent_keys:
            return True, send_key
    return False, "not due"


def send_daily_summary(analysis: dict[str, Any], state: dict[str, Any], config: dict[str, Any], dry_run: bool) -> tuple[bool, str]:
    locale = get_reply_language(config)
    should_send, reason = should_send_daily_summary(config, state)
    if not should_send:
        return False, reason
    send_key = reason
    summary_cfg = config.get("notification", {}).get("daily_summary", {})
    payload = build_daily_summary_payload(analysis, state, locale)
    llm_text: str | None = None
    usage: dict[str, Any] = {}
    llm_reason = "local fallback"
    if summary_cfg.get("llm_enabled", True):
        try:
            llm_text, usage = call_openclaw_llm_summary(build_daily_summary_prompt(payload, locale), config)
            llm_reason = "llm summary"
        except Exception as exc:
            llm_reason = f"llm failed: {exc}"
    message = llm_text or build_local_daily_summary(analysis, state, locale)
    media_path: Path | None = None
    if should_attach_chart(config) and summary_cfg.get("attach_chart", True):
        chart_cfg = config["notification"]["chart"]
        bars = max(int(chart_cfg.get("bars", 48)), 12)
        chart_path = resolve_project_path(chart_cfg.get("path", "state/latest-chart.svg"))
        chart_candles = enrich_candles(
            fetch_klines(config["symbol"], "15m", bars + 1, int(config["runtime"]["http_timeout_seconds"]))
        )[:-1]
        media_path = build_chart_svg(
            chart_candles,
            analysis["price"],
            chart_path,
            tr(locale, "ETHUSDT 每日市场评价", "ETHUSDT Daily Market Review"),
            locale,
        )
    result = send_imessage(config, message, dry_run=dry_run, media_path=media_path)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr, flush=True)
        return False, "daily summary send failed"
    daily_state = state.setdefault("daily_summary", {})
    sent_keys = [str(item) for item in daily_state.get("sent_keys", []) if str(item)]
    sent_keys.append(send_key)
    daily_state["sent_keys"] = sent_keys[-30:]
    daily_state["last_llm_usage"] = {
        "date": local_today(),
        "used_llm": bool(llm_text),
        "usage": usage,
        "reason": llm_reason,
    }
    return True, llm_reason


def signal_rank(label: str) -> int:
    return {"watch": 0, "near_buy": 1, "buy_trigger": 2}.get(label, 0)


def compact_alert_history(state: dict[str, Any]) -> None:
    history = state.get("alert_history", [])
    today = local_today()
    keep: list[dict[str, Any]] = []
    for item in history[-30:]:
        if item.get("date", today) >= today:
            keep.append(item)
    state["alert_history"] = keep


def position_is_open(state: dict[str, Any]) -> bool:
    return bool(state.get("position", {}).get("active"))


def position_entry_reference(state: dict[str, Any], fallback: float) -> float:
    entry_price = state.get("position", {}).get("entry_price")
    if isinstance(entry_price, (int, float)) and isfinite(float(entry_price)):
        return float(entry_price)
    return fallback


def start_tracking(state: dict[str, Any], analysis: dict[str, Any], config: dict[str, Any]) -> None:
    tracking_cfg = config.get("notification", {}).get("followup_tracking", {})
    if not tracking_cfg.get("enabled", False):
        state["tracking"] = {}
        return
    if tracking_cfg.get("only_when_position_open", False) and not position_is_open(state):
        state["tracking"] = {}
        return
    now_ts = time.time()
    state["tracking"] = {
        "active": True,
        "signal_key": analysis["signal_key"],
        "label": analysis["label"],
        "primary_signal": analysis["primary_signal"],
        "started_ts": now_ts,
        "expires_ts": now_ts + int(tracking_cfg["duration_minutes"]) * 60,
        "last_followup_ts": 0,
        "entry_reference": position_entry_reference(state, analysis.get("entry_reference", analysis["price"])),
        "anchor_price": analysis["price"],
        "followups_sent": 0,
    }


def clear_tracking_if_expired(state: dict[str, Any]) -> None:
    tracking = state.get("tracking", {})
    if not tracking.get("active"):
        return
    if time.time() >= float(tracking.get("expires_ts", 0)):
        state["tracking"] = {}


def should_send_alert(analysis: dict[str, Any], state: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    notification = config["notification"]
    if not notification.get("enabled", False):
        return False, "notification disabled"
    if analysis["label"] == "watch":
        return False, "watch only"
    if analysis["score"] < notification["min_score_to_alert"]:
        return False, "below min score"

    compact_alert_history(state)
    history = state.get("alert_history", [])
    alerts_today = [item for item in history if item.get("date") == local_today()]
    if len(alerts_today) >= int(notification["max_alerts_per_day"]):
        return False, "max daily alerts reached"
    if in_quiet_hours(config) and not (
        notification.get("quiet_hours", {}).get("override_for_buy_trigger", True) and analysis["label"] == "buy_trigger"
    ):
        return False, "quiet hours active"
    if not in_active_windows(config) and not (
        notification.get("active_windows", {}).get("override_for_buy_trigger", True) and analysis["label"] == "buy_trigger"
    ):
        return False, "outside active windows"

    last_sent = state.get("last_sent", {})
    if last_sent.get("signal_key") == analysis["signal_key"]:
        return False, "duplicate signal"

    now_ts = time.time()
    last_ts = float(last_sent.get("ts", 0))
    cooldown_seconds = notification["cooldown_minutes"] * 60
    upgraded = signal_rank(analysis["label"]) > signal_rank(str(last_sent.get("label", "watch")))
    same_signal = last_sent.get("primary_signal") == analysis["primary_signal"]
    last_price = float(last_sent.get("price", 0) or 0)
    repeat_move = abs(percent_change(last_price, analysis["price"])) if last_price else 999.0
    min_repeat_move = float(notification["min_price_move_percent_for_repeat"])

    if upgraded:
        return True, "signal upgraded"
    if same_signal and repeat_move < min_repeat_move:
        return False, "repeat move too small"
    if now_ts - last_ts >= cooldown_seconds:
        return True, "cooldown elapsed"
    return False, "cooldown active"


def send_imessage(
    config: dict[str, Any],
    message: str,
    dry_run: bool = False,
    media_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    notification = config["notification"]
    command = [
        resolve_openclaw_bin(),
        "message",
        "send",
        "--channel",
        notification["channel"],
        "--target",
        notification["target"],
        "--message",
        message,
        "--json",
    ]
    if media_path:
        command.extend(["--media", str(media_path)])
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=60, env=build_subprocess_env())


def maybe_send_followup(
    analysis: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
    dry_run: bool,
) -> tuple[bool, str]:
    tracking_cfg = config.get("notification", {}).get("followup_tracking", {})
    locale = get_reply_language(config)
    if not tracking_cfg.get("enabled", False):
        return False, "followup disabled"
    clear_tracking_if_expired(state)
    tracking = state.get("tracking", {})
    if not tracking.get("active"):
        return False, "no active tracking"
    now_ts = time.time()
    last_followup_ts = float(tracking.get("last_followup_ts", 0))
    interval_seconds = int(tracking_cfg["interval_minutes"]) * 60
    if now_ts - last_followup_ts < interval_seconds:
        return False, "followup interval active"
    anchor_price = float(tracking.get("anchor_price", analysis["price"]))
    move_pct = abs(percent_change(anchor_price, analysis["price"]))
    if move_pct < float(tracking_cfg["min_move_percent"]):
        return False, "followup move too small"

    message = build_followup_message(analysis, tracking, locale)
    media_path: Path | None = None
    if should_attach_chart(config):
        chart_cfg = config["notification"]["chart"]
        bars = max(int(chart_cfg.get("bars", 48)), 12)
        chart_path = resolve_project_path(chart_cfg.get("path", "state/latest-chart.svg"))
        chart_candles = enrich_candles(
            fetch_klines(
                config["symbol"],
                "15m",
                bars + 1,
                int(config["runtime"]["http_timeout_seconds"]),
            )
        )[:-1]
        media_path = build_chart_svg(
            chart_candles,
            analysis["price"],
            chart_path,
            tr(locale, f"ETHUSDT 跟踪 {localize_signal_name(analysis['primary_signal'], locale)}", f"ETHUSDT Follow-up {localize_signal_name(analysis['primary_signal'], locale)}"),
            locale,
        )
    result = send_imessage(config, message, dry_run=dry_run, media_path=media_path)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr, flush=True)
        return False, "followup send failed"

    tracking["last_followup_ts"] = now_ts
    tracking["anchor_price"] = analysis["price"]
    tracking["followups_sent"] = int(tracking.get("followups_sent", 0)) + 1
    state["tracking"] = tracking
    return True, "followup sent"


def print_text_snapshot(analysis: dict[str, Any], locale: str) -> None:
    print(build_message(analysis, locale), flush=True)
    print("", flush=True)
    print(tr(locale, "关键指标:", "Key metrics:"), flush=True)
    print(json.dumps(analysis["metrics"], indent=2, ensure_ascii=False), flush=True)
    print("", flush=True)
    print(tr(locale, "各信号评分:", "Signal scores:"), flush=True)
    signal_scores = {name: payload["score"] for name, payload in analysis["signals"].items()}
    print(json.dumps(signal_scores, indent=2, ensure_ascii=False), flush=True)


def run_chat_query(
    config_path: Path,
    state_path: Path,
    message: str,
    sender: str = "",
    message_id: str = "",
) -> int:
    del sender
    config = load_config(config_path)
    locale = get_reply_language(config)
    state = load_state(state_path)
    if is_chat_message_processed(state, message_id):
        print(json.dumps({"matched": False, "reason": "duplicate message"}, ensure_ascii=False), flush=True)
        return 0

    intents = extract_chat_intents(message)
    if not intents:
        print(json.dumps({"matched": False, "reason": "no supported intent"}, ensure_ascii=False), flush=True)
        return 0

    try:
        analysis = analyze_market(config, state)
    except Exception as exc:
        state["last_analysis"] = {
            "generated_at": utc_now_iso(),
            "status": "error",
            "error": str(exc),
        }
        save_json_file(state_path, state)
        reply = tr(locale, f"【ETH问答】暂时无法分析行情：{exc}", f"[ETH Assistant] Market analysis is temporarily unavailable: {exc}")
        print(json.dumps({"matched": True, "reply": reply}, ensure_ascii=False), flush=True)
        return 0

    state["last_analysis"] = analysis
    reply = build_chat_reply(message, analysis, state, locale)
    if not reply:
        print(json.dumps({"matched": False, "reason": "intent not actionable"}, ensure_ascii=False), flush=True)
        save_json_file(state_path, state)
        return 0

    mark_chat_message_processed(state, message_id)
    save_json_file(state_path, state)
    print(json.dumps({"matched": True, "reply": reply}, ensure_ascii=False), flush=True)
    return 0


def run_once(config_path: Path, state_path: Path, send: bool, dry_run: bool) -> int:
    config = load_config(config_path)
    locale = get_reply_language(config)
    state = load_state(state_path)
    try:
        analysis = analyze_market(config, state)
    except Exception as exc:
        state["last_analysis"] = {
            "generated_at": utc_now_iso(),
            "status": "error",
            "error": str(exc),
        }
        save_json_file(state_path, state)
        print(f"ETH watcher error: {exc}", file=sys.stderr, flush=True)
        return 1

    state["last_analysis"] = analysis
    clear_tracking_if_expired(state)

    if send:
        should_send, reason = should_send_alert(analysis, state, config)
        if should_send:
            message = build_message(analysis, locale)
            media_path: Path | None = None
            if should_attach_chart(config):
                chart_cfg = config["notification"]["chart"]
                bars = max(int(chart_cfg.get("bars", 48)), 12)
                chart_path = resolve_project_path(chart_cfg.get("path", "state/latest-chart.svg"))
                chart_candles = enrich_candles(
                    fetch_klines(config["symbol"], "15m", bars + 1, int(config["runtime"]["http_timeout_seconds"]))
                )[:-1]
                media_path = build_chart_svg(
                    chart_candles,
                    analysis["price"],
                    chart_path,
                    f"ETHUSDT {localize_signal_name(analysis['primary_signal'], locale)}",
                    locale,
                )
            result = send_imessage(config, message, dry_run=dry_run, media_path=media_path)
            if result.returncode != 0:
                print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr, flush=True)
                save_json_file(state_path, state)
                return result.returncode
            sent_meta = {
                "ts": time.time(),
                "date": local_today(),
                "generated_at": analysis["generated_at"],
                "signal_key": analysis["signal_key"],
                "label": analysis["label"],
                "primary_signal": analysis["primary_signal"],
                "price": analysis["price"],
            }
            state["last_sent"] = sent_meta
            state.setdefault("alert_history", []).append(sent_meta)
            compact_alert_history(state)
            start_tracking(state, analysis, config)
            print(f"alert sent: {reason}", flush=True)
        else:
            followup_sent, followup_reason = maybe_send_followup(analysis, state, config, dry_run=dry_run)
            if followup_sent:
                print(f"followup sent: {followup_reason}", flush=True)
            else:
                print(f"no alert sent: {reason}; followup: {followup_reason}", flush=True)
        daily_sent, daily_reason = send_daily_summary(analysis, state, config, dry_run=dry_run)
        if daily_sent:
            print(f"daily summary sent: {daily_reason}", flush=True)
    else:
        print_text_snapshot(analysis, locale)

    save_json_file(state_path, state)
    return 0


def run_daemon(config_path: Path, state_path: Path) -> int:
    stop = {"value": False}

    def handle_signal(signum: int, _frame: Any) -> None:
        stop["value"] = True
        print(f"received signal {signum}, shutting down", flush=True)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stop["value"]:
        exit_code = run_once(config_path, state_path, send=True, dry_run=False)
        if exit_code != 0:
            time.sleep(30)
            continue
        config = load_config(config_path)
        time.sleep(int(config["runtime"]["poll_interval_seconds"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETH watcher for OpenClaw")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("snapshot", help="Print the current ETH analysis")

    run_once_parser = subparsers.add_parser("run-once", help="Run one analysis cycle")
    run_once_parser.add_argument("--send", action="store_true", help="Send alert when rules match")
    run_once_parser.add_argument("--dry-run", action="store_true", help="Dry-run message delivery")

    daemon_parser = subparsers.add_parser("daemon", help="Run the watcher loop")
    daemon_parser.add_argument("--foreground", action="store_true", help="Unused compatibility flag")

    chat_query = subparsers.add_parser("chat-query", help="Reply to supported ETH chat questions")
    chat_query.add_argument("--message", required=True, help="Inbound chat message body")
    chat_query.add_argument("--sender", default="", help="Inbound sender identifier")
    chat_query.add_argument("--message-id", default="", help="Inbound provider message id")

    send_test = subparsers.add_parser("send-test", help="Send a test iMessage payload")
    send_test.add_argument("--dry-run", action="store_true", help="Dry-run only")

    position_open = subparsers.add_parser("position-open", help="Record an open position for follow-up tracking")
    position_open.add_argument("--entry-price", type=float, required=True, help="Actual filled entry price")
    position_open.add_argument("--size", default="", help="Optional size hint, e.g. 10%")
    position_open.add_argument("--notes", default="", help="Optional notes")

    subparsers.add_parser("position-close", help="Clear the active position")
    subparsers.add_parser("position-status", help="Show the current position state")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config_path = resolve_project_path(args.config)
    state_path = resolve_project_path(args.state)

    if args.command == "snapshot":
        return run_once(config_path, state_path, send=False, dry_run=False)
    if args.command == "run-once":
        return run_once(config_path, state_path, send=args.send, dry_run=args.dry_run)
    if args.command == "daemon":
        return run_daemon(config_path, state_path)
    if args.command == "chat-query":
        return run_chat_query(
            config_path,
            state_path,
            message=str(args.message or ""),
            sender=str(args.sender or ""),
            message_id=str(args.message_id or ""),
        )
    if args.command == "position-open":
        state = load_state(state_path)
        state["position"] = {
            "active": True,
            "entry_price": round(float(args.entry_price), 2),
            "size_hint": str(args.size or ""),
            "opened_at": utc_now_iso(),
            "notes": str(args.notes or ""),
        }
        save_json_file(state_path, state)
        print(json.dumps(state["position"], indent=2, ensure_ascii=False), flush=True)
        return 0
    if args.command == "position-close":
        state = load_state(state_path)
        state["position"] = {
            "active": False,
            "entry_price": None,
            "size_hint": "",
            "opened_at": None,
            "notes": "",
        }
        state["tracking"] = {}
        save_json_file(state_path, state)
        print("position cleared", flush=True)
        return 0
    if args.command == "position-status":
        state = load_state(state_path)
        print(json.dumps(state.get("position", {}), indent=2, ensure_ascii=False), flush=True)
        return 0
    if args.command == "send-test":
        config = load_config(config_path)
        locale = get_reply_language(config)
        state = load_state(state_path)
        payload = build_message(
            {
                "label": "near_buy",
                "price": 1992.80,
                "primary_signal": "pullback",
                "strategy_profile": config["strategy_profile"],
                "market_regime": "偏多",
                "score": 76,
                "entry_reference": 1990.5,
                "entry_plan": {
                    "kind": "range",
                    "low": 1988.0,
                    "high": 1993.0,
                    "trigger": None,
                    "reference": 1990.5,
                },
                "position_size_hint": "8%-12%",
                "stop_loss": 1978.50,
                "take_profit": {
                    "tp1": 2008.5,
                    "tp2": 2018.1,
                    "rr_to_tp1": 1.5,
                    "rr_to_tp2": 2.3,
                },
                "position_active": position_is_open(state),
                "position_entry_price": position_entry_reference(state, 1990.5),
                "reasons": [
                    "1h 与 15m 均线同向，趋势配合",
                    "价格回到 EMA20 附近，追高风险较低",
                    "RSI 回升，短线动能在恢复",
                ],
            },
            locale,
        )
        media_path: Path | None = None
        if should_attach_chart(config):
            chart_cfg = config["notification"]["chart"]
            sample_closes = [1982.4, 1984.8, 1986.0, 1985.2, 1987.6, 1989.1, 1990.3, 1992.8]
            sample_candles = []
            prev = sample_closes[0] - 1.0
            for close in sample_closes:
                open_price = prev
                high = max(open_price, close) + 1.2
                low = min(open_price, close) - 1.0
                sample_candles.append(
                    {
                        "open": open_price,
                        "high": high,
                        "low": low,
                        "close": close,
                    }
                )
                prev = close
            media_path = build_chart_svg(
                sample_candles,
                1992.8,
                resolve_project_path(chart_cfg.get("path", "state/latest-chart.svg")),
                tr(locale, "ETHUSDT 样例提醒", "ETHUSDT Sample Alert"),
                locale,
            )
        result = send_imessage(config, payload, dry_run=args.dry_run, media_path=media_path)
        if result.returncode != 0:
            print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr, flush=True)
            return result.returncode
        print(result.stdout.strip(), flush=True)
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
