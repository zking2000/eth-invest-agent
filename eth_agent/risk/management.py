from __future__ import annotations

import time
from math import isfinite
from typing import Any

from eth_agent.features.indicators import percent_change
from eth_agent.utils.time import local_today


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


def build_risk_plan(entry_price: float, stop_loss: float, take_profit: float, risk_budget: float, capital: float) -> dict[str, float]:
    per_unit_risk = max(abs(entry_price - stop_loss), 1e-9)
    position_units = risk_budget / per_unit_risk if risk_budget > 0 else 0.0
    position_notional = position_units * entry_price
    rr = abs((take_profit - entry_price) / per_unit_risk)
    capital_fraction = position_notional / capital if capital > 0 else 0.0
    return {
        "entry_price": round(entry_price, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "risk_budget": round(risk_budget, 4),
        "position_units": round(position_units, 6),
        "position_notional": round(position_notional, 2),
        "capital_fraction": round(capital_fraction, 4),
        "risk_reward": round(rr, 4),
    }


def should_send_alert(analysis: dict[str, Any], state: dict[str, Any], config: dict[str, Any], *, in_quiet_hours: bool, in_active_windows: bool) -> tuple[bool, str]:
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
    if in_quiet_hours and not (notification.get("quiet_hours", {}).get("override_for_buy_trigger", True) and analysis["label"] == "buy_trigger"):
        return False, "quiet hours active"
    if not in_active_windows and not (notification.get("active_windows", {}).get("override_for_buy_trigger", True) and analysis["label"] == "buy_trigger"):
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
