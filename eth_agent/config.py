from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from eth_agent.utils.io import save_json_file, load_json_file


BINANCE_BASE_URLS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
]


def resolve_project_dir() -> Path:
    env_path = os.environ.get("ETH_AGENT_HOME")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def infer_default_config_path(project_dir: Path) -> Path:
    local_path = project_dir / "config.local.json"
    if local_path.exists():
        return local_path
    return project_dir / "config.json"


PROJECT_DIR = resolve_project_dir()
DEFAULT_CONFIG_PATH = infer_default_config_path(PROJECT_DIR)
DEFAULT_STATE_PATH = PROJECT_DIR / "state" / "runtime.json"


def resolve_project_path(path_value: str | Path, *, base_dir: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir or PROJECT_DIR) / path


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


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override)


DEFAULT_CONFIG: dict[str, Any] = {
    "symbol": "ETHUSDT",
    "strategy_profile": "balanced",
    "runtime": {
        "poll_interval_seconds": 60,
        "http_timeout_seconds": 12,
        "kline_limit": 320,
    },
    "display": {
        "price_currency": "CNY",
        "usd_cny_rate": 7.20,
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
    "ml": {
        "enabled": True,
        "model_path": "models/xgboost_eth_signal.json",
        "metadata_path": "models/xgboost_eth_signal.meta.json",
        "feature_limit": 500,
        "target_horizon": 4,
        "target_threshold_pct": 0.35,
        "backtest_defaults": {
            "cash": 10000.0,
            "commission": 0.001,
            "risk_fraction": 0.01,
            "stop_loss_atr": 1.3,
            "take_profit_rr": 1.5,
            "entry_prob_threshold": 0.30,
            "exit_prob_threshold": 0.36,
            "min_hold_bars": 1,
        },
        "sweep_defaults": {
            "entry_prob_thresholds": [0.28, 0.30, 0.34],
            "exit_prob_thresholds": [0.34, 0.36, 0.42],
            "stop_loss_atrs": [1.0, 1.3, 1.6],
            "top": 10,
        },
    },
    "profiles": {
        "scalp": {
            "runtime": {"poll_interval_seconds": 45},
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
            "runtime": {"poll_interval_seconds": 60},
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
            "runtime": {"poll_interval_seconds": 90},
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
