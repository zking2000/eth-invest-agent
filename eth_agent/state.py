from __future__ import annotations

from pathlib import Path
from typing import Any

from eth_agent.utils.io import load_json_file


DEFAULT_STATE: dict[str, Any] = {
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
    "chat": {"processed_message_ids": []},
    "daily_summary": {
        "sent_keys": [],
        "last_llm_usage": {},
        "audit_history": [],
        "last_audit": {},
    },
}


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
    state.setdefault("chat", {"processed_message_ids": []})
    state.setdefault(
        "daily_summary",
        {
            "sent_keys": [],
            "last_llm_usage": {},
            "audit_history": [],
            "last_audit": {},
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
    audit_history = daily_summary.get("audit_history", [])
    if not isinstance(audit_history, list):
        audit_history = []
    daily_summary["audit_history"] = [item for item in audit_history[-30:] if isinstance(item, dict)]
    if not isinstance(daily_summary.get("last_audit"), dict):
        daily_summary["last_audit"] = {}
    return state


def load_state(path: Path) -> dict[str, Any]:
    return ensure_state_defaults(load_json_file(path, DEFAULT_STATE))
