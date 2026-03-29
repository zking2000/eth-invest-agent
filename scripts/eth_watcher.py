#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import math
import signal
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from math import isfinite
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from eth_agent.config import load_config as module_load_config
from eth_agent.config import resolve_project_path as module_resolve_project_path
from eth_agent.data.binance import fetch_klines as module_fetch_klines
from eth_agent.data.binance import fetch_price as module_fetch_price
from eth_agent.features.indicators import clamp as module_clamp
from eth_agent.features.indicators import enrich_candles as module_enrich_candles
from eth_agent.features.indicators import fmt_price as module_fmt_price
from eth_agent.features.indicators import percent_change as module_percent_change
from eth_agent.features.pipeline import FeatureConfig, build_feature_frame, load_feature_frame, save_feature_frame
from eth_agent.i18n import get_reply_language as module_get_reply_language
from eth_agent.i18n import localize_market_regime as module_localize_market_regime
from eth_agent.i18n import localize_reason as module_localize_reason
from eth_agent.i18n import localize_reasons as module_localize_reasons
from eth_agent.i18n import localize_signal_name as module_localize_signal_name
from eth_agent.i18n import strength_label as module_strength_label
from eth_agent.i18n import tr as module_tr
from eth_agent.risk.management import clear_tracking_if_expired as module_clear_tracking_if_expired
from eth_agent.risk.management import compact_alert_history as module_compact_alert_history
from eth_agent.risk.management import position_entry_reference as module_position_entry_reference
from eth_agent.risk.management import position_is_open as module_position_is_open
from eth_agent.risk.management import should_send_alert as module_should_send_alert
from eth_agent.risk.management import start_tracking as module_start_tracking
from eth_agent.state import ensure_state_defaults as module_ensure_state_defaults
from eth_agent.state import load_state as module_load_state
from eth_agent.strategy.rule_engine import analyze_market as module_analyze_market
from eth_agent.utils.time import local_hour as module_local_hour
from eth_agent.utils.time import local_minute_of_day as module_local_minute_of_day
from eth_agent.utils.time import local_today as module_local_today
from eth_agent.utils.time import parse_hhmm as module_parse_hhmm
from eth_agent.utils.time import utc_now_iso as module_utc_now_iso
from eth_agent.visualization.charts import build_chart_svg as module_build_chart_svg


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


def resolve_openclaw_bin() -> str:
    env_value = os.environ.get("OPENCLAW_BIN")
    if env_value:
        return env_value
    discovered = shutil.which("openclaw")
    if discovered:
        return discovered
    return "/opt/homebrew/bin/openclaw"


def fetch_live_usd_cny_rate(timeout_seconds: int = 12) -> tuple[float, str]:
    request = urllib.request.Request(
        "https://open.er-api.com/v6/latest/USD",
        headers={"User-Agent": "eth-invest-agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=max(int(timeout_seconds), 3)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rate = float(payload.get("rates", {}).get("CNY", 0.0))
    if not isfinite(rate) or rate <= 0:
        raise RuntimeError("live USD/CNY rate missing from response")
    return rate, str(payload.get("provider", "open.er-api.com"))


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


def default_model_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    ml_cfg = config.get("ml", {})
    model_path = module_resolve_project_path(ml_cfg.get("model_path", "models/xgboost_eth_signal.json"))
    metadata_path = module_resolve_project_path(ml_cfg.get("metadata_path", "models/xgboost_eth_signal.meta.json"))
    return model_path, metadata_path


def maybe_enrich_analysis_with_ml(analysis: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    ml_cfg = config.get("ml", {})
    if not ml_cfg.get("enabled", True):
        return analysis
    model_path, metadata_path = default_model_paths(config)
    if not model_path.exists() or not metadata_path.exists():
        return analysis
    try:
        from eth_agent.models.xgboost_model import XGBoostSignalModel

        timeout_seconds = int(config["runtime"]["http_timeout_seconds"])
        limit = max(int(ml_cfg.get("feature_limit", 500)), 160)
        candles = module_fetch_klines(config["symbol"], "15m", limit, timeout_seconds)
        frame = pd.DataFrame(candles)
        feature_frame = build_feature_frame(
            frame,
            FeatureConfig(
                target_horizon=int(ml_cfg.get("target_horizon", 4)),
                target_threshold_pct=float(ml_cfg.get("target_threshold_pct", 0.35)),
            ),
        )
        model = XGBoostSignalModel.load(model_path, metadata_path)
        prediction = model.predict_latest(feature_frame)
        analysis["rule_score"] = int(analysis["score"])
        analysis["ml_prediction"] = prediction
        up_prob = float(prediction["probabilities"]["up"])
        down_prob = float(prediction["probabilities"]["down"])
        adjustment = 0
        if analysis["label"] != "watch":
            if prediction["predicted_signal"] > 0:
                adjustment = min(int(round(up_prob * 12)), 10)
            elif prediction["predicted_signal"] < 0:
                adjustment = -min(int(round(down_prob * 14)), 12)
        analysis["model_score_adjustment"] = adjustment
        analysis["score"] = max(0, min(100, int(analysis["score"]) + adjustment))
        if analysis["label"] == "near_buy" and prediction["predicted_signal"] > 0 and up_prob >= 0.66 and analysis["score"] >= int(config["rules"]["buy_trigger_score"]):
            analysis["label"] = "buy_trigger"
        if analysis["label"] == "buy_trigger" and prediction["predicted_signal"] < 0 and down_prob >= 0.55:
            analysis["label"] = "near_buy"
        analysis["signal_key"] = f"{analysis['label']}:{analysis['primary_signal']}:{analysis['generated_at']}"
    except Exception as exc:
        analysis["ml_prediction_error"] = str(exc)
    return analysis


def run_download_history(args: argparse.Namespace) -> int:
    from eth_agent.data.ccxt_provider import CCXTDataProvider, CCXTDownloadRequest

    provider = CCXTDataProvider(exchange_id=str(args.exchange))
    request = CCXTDownloadRequest(
        exchange_id=str(args.exchange),
        symbol=str(args.symbol),
        timeframe=str(args.timeframe),
        limit=int(args.limit),
        since_ms=int(args.since_ms) if args.since_ms else None,
    )
    frame = provider.fetch_ohlcv_frame(request)
    output_path = module_resolve_project_path(str(args.output))
    provider.save_frame(frame, output_path)
    print(json.dumps({"rows": len(frame), "output": str(output_path)}, ensure_ascii=False), flush=True)
    return 0


def run_build_features(args: argparse.Namespace) -> int:
    input_path = module_resolve_project_path(str(args.input))
    output_path = module_resolve_project_path(str(args.output))
    frame = pd.read_csv(input_path, parse_dates=["timestamp"] if "timestamp" in pd.read_csv(input_path, nrows=0).columns else None)
    feature_frame = build_feature_frame(
        frame,
        FeatureConfig(target_horizon=int(args.horizon), target_threshold_pct=float(args.threshold_pct)),
    )
    save_feature_frame(feature_frame, output_path)
    print(json.dumps({"rows": len(feature_frame), "output": str(output_path)}, ensure_ascii=False), flush=True)
    return 0


def run_train_model_cmd(args: argparse.Namespace) -> int:
    from eth_agent.models.xgboost_model import TrainConfig, XGBoostSignalModel

    features_path = module_resolve_project_path(str(args.features))
    model_path = module_resolve_project_path(str(args.model_output))
    metadata_path = module_resolve_project_path(str(args.metadata_output))
    frame = load_feature_frame(features_path)
    model = XGBoostSignalModel.train(
        frame,
        TrainConfig(
            max_depth=int(args.max_depth),
            learning_rate=float(args.learning_rate),
            n_estimators=int(args.n_estimators),
        ),
    )
    model.save(model_path, metadata_path)
    latest_prediction = model.predict_latest(frame.dropna())
    print(
        json.dumps(
            {
                "model": str(model_path),
                "metadata": str(metadata_path),
                "feature_count": len(model.features),
                "rows": model.metadata.get("rows"),
                "latest_prediction": latest_prediction,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


def run_backtest_cmd(args: argparse.Namespace) -> int:
    from eth_agent.backtest.engine import BacktestConfig, run_backtest
    from eth_agent.features.pipeline import build_feature_frame
    from eth_agent.models.xgboost_model import XGBoostSignalModel

    config = load_config(module_resolve_project_path(str(args.config)))
    defaults = config_backtest_defaults(config)
    input_path = module_resolve_project_path(str(args.input))
    model_path = module_resolve_project_path(str(args.model_path))
    metadata_path = module_resolve_project_path(str(args.metadata_path))
    frame = pd.read_csv(input_path, parse_dates=["timestamp"] if "timestamp" in pd.read_csv(input_path, nrows=0).columns else None)
    model = XGBoostSignalModel.load(model_path, metadata_path)
    result = run_backtest(
        frame,
        model,
        BacktestConfig(
            initial_cash=float(resolve_numeric_arg(args.cash, float(defaults.get("cash", 10000.0)))),
            commission=float(resolve_numeric_arg(args.commission, float(defaults.get("commission", 0.001)))),
            risk_fraction=float(resolve_numeric_arg(args.risk_fraction, float(defaults.get("risk_fraction", 0.01)))),
            stop_loss_atr=float(resolve_numeric_arg(args.stop_loss_atr, float(defaults.get("stop_loss_atr", 1.3)))),
            take_profit_rr=float(resolve_numeric_arg(args.take_profit_rr, float(defaults.get("take_profit_rr", 1.5)))),
            entry_prob_threshold=float(resolve_numeric_arg(args.entry_prob_threshold, float(defaults.get("entry_prob_threshold", 0.30)))),
            exit_prob_threshold=float(resolve_numeric_arg(args.exit_prob_threshold, float(defaults.get("exit_prob_threshold", 0.36)))),
            min_hold_bars=int(resolve_numeric_arg(args.min_hold_bars, int(defaults.get("min_hold_bars", 1)))),
        ),
    )
    if not bool(args.no_report):
        report_dir = resolve_report_dir(str(args.report_dir), prefix="backtest")
        scored_frame = model.score_frame(build_feature_frame(frame))
        save_backtest_report(report_dir, result, scored_frame)
        update_latest_report_link(report_dir, prefix="backtest")
        result["report_dir"] = str(report_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_float_grid(value: str) -> list[float]:
    items = [item.strip() for item in str(value).split(",")]
    return [float(item) for item in items if item]


def config_backtest_defaults(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("ml", {}).get("backtest_defaults", {}))


def config_sweep_defaults(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("ml", {}).get("sweep_defaults", {}))


def resolve_numeric_arg(value: Any, fallback: float | int) -> float | int:
    return fallback if value is None else value


def resolve_grid_arg(value: str | None, fallback: list[float]) -> list[float]:
    if value is None or str(value).strip() == "":
        return [float(item) for item in fallback]
    return parse_float_grid(str(value))


def resolve_report_dir(value: str | None, *, prefix: str) -> Path:
    if value:
        return module_resolve_project_path(value)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return module_resolve_project_path(f"reports/{prefix}/{stamp}")


def update_latest_report_link(report_dir: Path, *, prefix: str) -> None:
    latest_root = module_resolve_project_path("reports/latest")
    latest_link = latest_root / prefix
    latest_root.mkdir(parents=True, exist_ok=True)
    if latest_link.is_symlink() or latest_link.exists():
        if latest_link.is_dir() and not latest_link.is_symlink():
            shutil.rmtree(latest_link)
        else:
            latest_link.unlink()
    latest_link.symlink_to(report_dir.resolve(), target_is_directory=True)


def apply_best_sweep_to_config(config_path: Path, best_result: dict[str, Any], *, args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(config_path)
    ml_cfg = config.setdefault("ml", {})
    backtest_defaults = ml_cfg.setdefault("backtest_defaults", {})
    sweep_defaults = ml_cfg.setdefault("sweep_defaults", {})
    top_value = int(args.top) if args.top is not None else int(sweep_defaults.get("top", 10))
    backtest_defaults.update(
        {
            "cash": float(resolve_numeric_arg(args.cash, float(backtest_defaults.get("cash", 10000.0)))),
            "commission": float(resolve_numeric_arg(args.commission, float(backtest_defaults.get("commission", 0.001)))),
            "risk_fraction": float(resolve_numeric_arg(args.risk_fraction, float(backtest_defaults.get("risk_fraction", 0.01)))),
            "take_profit_rr": float(resolve_numeric_arg(args.take_profit_rr, float(backtest_defaults.get("take_profit_rr", 1.5)))),
            "min_hold_bars": int(resolve_numeric_arg(args.min_hold_bars, int(backtest_defaults.get("min_hold_bars", 1)))),
            "entry_prob_threshold": float(best_result["entry_prob_threshold"]),
            "exit_prob_threshold": float(best_result["exit_prob_threshold"]),
            "stop_loss_atr": float(best_result["stop_loss_atr"]),
            "selected_at": utc_now_iso(),
        }
    )
    sweep_defaults.update(
        {
            "entry_prob_thresholds": parse_float_grid(str(args.entry_prob_thresholds))
            if getattr(args, "entry_prob_thresholds", None)
            else sweep_defaults.get("entry_prob_thresholds", []),
            "exit_prob_thresholds": parse_float_grid(str(args.exit_prob_thresholds))
            if getattr(args, "exit_prob_thresholds", None)
            else sweep_defaults.get("exit_prob_thresholds", []),
            "stop_loss_atrs": parse_float_grid(str(args.stop_loss_atrs))
            if getattr(args, "stop_loss_atrs", None)
            else sweep_defaults.get("stop_loss_atrs", []),
            "top": top_value,
            "last_selected_at": utc_now_iso(),
        }
    )
    ml_cfg["recommended_backtest"] = {
        "entry_prob_threshold": float(best_result["entry_prob_threshold"]),
        "exit_prob_threshold": float(best_result["exit_prob_threshold"]),
        "stop_loss_atr": float(best_result["stop_loss_atr"]),
        "return_pct": float(best_result["return_pct"]),
        "win_rate_pct": float(best_result["win_rate_pct"]),
        "max_drawdown_pct": float(best_result["max_drawdown_pct"]),
        "sharpe_ratio": float(best_result["sharpe_ratio"]),
        "total_trades": int(best_result["total_trades"]),
        "selected_at": utc_now_iso(),
    }
    save_json_file(config_path, config)
    return config


def sanitize_result_for_json(result: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(result, ensure_ascii=False))
    return payload


def save_backtest_report(report_dir: Path, result: dict[str, Any], scored_frame: pd.DataFrame) -> None:
    from eth_agent.visualization.charts import plot_backtest_results, plot_monthly_returns, plot_price_with_signals

    ensure_parent(report_dir / "summary.json")
    summary_path = report_dir / "summary.json"
    trades_path = report_dir / "trades.csv"
    equity_path = report_dir / "equity_curve.csv"
    price_chart_path = report_dir / "price_signals.png"
    equity_chart_path = report_dir / "equity_curve.png"
    monthly_chart_path = report_dir / "monthly_returns.png"

    save_json_file(summary_path, sanitize_result_for_json(result))
    pd.DataFrame(result.get("trades", [])).to_csv(trades_path, index=False)
    equity_frame = pd.DataFrame(result.get("equity_curve", []))
    equity_frame.to_csv(equity_path, index=False)
    plot_price_with_signals(scored_frame, price_chart_path)
    if not equity_frame.empty and "equity" in equity_frame.columns:
        plot_backtest_results(equity_frame["equity"], equity_chart_path)
    plot_monthly_returns(list(result.get("monthly_returns", [])), monthly_chart_path)


def run_sweep_backtest_cmd(args: argparse.Namespace) -> int:
    from eth_agent.backtest.engine import BacktestConfig, run_backtest
    from eth_agent.models.xgboost_model import XGBoostSignalModel
    from eth_agent.visualization.charts import plot_parameter_sweep_heatmap

    config = load_config(module_resolve_project_path(str(args.config)))
    backtest_defaults = config_backtest_defaults(config)
    sweep_defaults = config_sweep_defaults(config)
    input_path = module_resolve_project_path(str(args.input))
    model_path = module_resolve_project_path(str(args.model_path))
    metadata_path = module_resolve_project_path(str(args.metadata_path))
    frame = pd.read_csv(input_path, parse_dates=["timestamp"] if "timestamp" in pd.read_csv(input_path, nrows=0).columns else None)
    model = XGBoostSignalModel.load(model_path, metadata_path)

    entry_thresholds = resolve_grid_arg(args.entry_prob_thresholds, list(sweep_defaults.get("entry_prob_thresholds", [0.28, 0.30, 0.34])))
    exit_thresholds = resolve_grid_arg(args.exit_prob_thresholds, list(sweep_defaults.get("exit_prob_thresholds", [0.34, 0.36, 0.42])))
    stop_loss_atrs = resolve_grid_arg(args.stop_loss_atrs, list(sweep_defaults.get("stop_loss_atrs", [1.0, 1.3, 1.6])))

    results: list[dict[str, Any]] = []
    for stop_loss_atr in stop_loss_atrs:
        for entry_threshold in entry_thresholds:
            for exit_threshold in exit_thresholds:
                run_result = run_backtest(
                    frame,
                    model,
                    BacktestConfig(
                        initial_cash=float(resolve_numeric_arg(args.cash, float(backtest_defaults.get("cash", 10000.0)))),
                        commission=float(resolve_numeric_arg(args.commission, float(backtest_defaults.get("commission", 0.001)))),
                        risk_fraction=float(resolve_numeric_arg(args.risk_fraction, float(backtest_defaults.get("risk_fraction", 0.01)))),
                        stop_loss_atr=float(stop_loss_atr),
                        take_profit_rr=float(resolve_numeric_arg(args.take_profit_rr, float(backtest_defaults.get("take_profit_rr", 1.5)))),
                        entry_prob_threshold=float(entry_threshold),
                        exit_prob_threshold=float(exit_threshold),
                        min_hold_bars=int(resolve_numeric_arg(args.min_hold_bars, int(backtest_defaults.get("min_hold_bars", 1)))),
                    ),
                )
                results.append(
                    {
                        "entry_prob_threshold": float(entry_threshold),
                        "exit_prob_threshold": float(exit_threshold),
                        "stop_loss_atr": float(stop_loss_atr),
                        "return_pct": float(run_result["return_pct"]),
                        "win_rate_pct": float(run_result["win_rate_pct"]),
                        "max_drawdown_pct": float(run_result["max_drawdown_pct"]),
                        "sharpe_ratio": float(run_result["sharpe_ratio"]),
                        "total_trades": int(run_result["total_trades"]),
                    }
                )

    result_frame = pd.DataFrame(results).sort_values(
        ["return_pct", "sharpe_ratio", "win_rate_pct", "total_trades"],
        ascending=[False, False, False, False],
    )
    top_n = max(int(resolve_numeric_arg(args.top, int(sweep_defaults.get("top", 10)))), 1)
    summary = {
        "tested_runs": int(len(result_frame)),
        "top_results": result_frame.head(top_n).to_dict(orient="records"),
        "all_results": result_frame.to_dict(orient="records"),
    }
    if not result_frame.empty:
        best_result = result_frame.iloc[0].to_dict()
        summary["recommended_defaults"] = best_result
        if not bool(args.no_apply_best_to_config):
            apply_best_sweep_to_config(module_resolve_project_path(str(args.config)), best_result, args=args)
            summary["config_updated"] = str(module_resolve_project_path(str(args.config)))
    if not bool(args.no_report):
        report_dir = resolve_report_dir(str(args.report_dir), prefix="sweeps")
        ensure_parent(report_dir / "summary.json")
        save_json_file(report_dir / "summary.json", summary)
        result_frame.to_csv(report_dir / "grid.csv", index=False)
        for stop_loss_atr in stop_loss_atrs:
            slice_frame = result_frame[result_frame["stop_loss_atr"] == float(stop_loss_atr)]
            if slice_frame.empty:
                continue
            pivot = slice_frame.pivot(
                index="entry_prob_threshold",
                columns="exit_prob_threshold",
                values="return_pct",
            ).sort_index().sort_index(axis=1)
            plot_parameter_sweep_heatmap(
                pivot,
                report_dir / f"heatmap_return_stop_{stop_loss_atr:.2f}.png",
                value_column="return_pct",
                title=f"Return Heatmap (stop_loss_atr={stop_loss_atr:.2f})",
                xlabel="Exit Threshold",
                ylabel="Entry Threshold",
            )
        update_latest_report_link(report_dir, prefix="sweeps")
        summary["report_dir"] = str(report_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def utc_now_iso() -> str:
    return module_utc_now_iso()


def local_today() -> str:
    return module_local_today()


def local_hour() -> int:
    return module_local_hour()


def local_minute_of_day() -> int:
    return module_local_minute_of_day()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def save_json_file(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def load_config(path: Path) -> dict[str, Any]:
    return module_load_config(path)


def load_state(path: Path) -> dict[str, Any]:
    return module_load_state(path)


def ensure_state_defaults(state: dict[str, Any]) -> dict[str, Any]:
    return module_ensure_state_defaults(state)


def fetch_price(symbol: str, timeout_seconds: int) -> float:
    return module_fetch_price(symbol, timeout_seconds)


def fetch_klines(symbol: str, interval: str, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    return module_fetch_klines(symbol, interval, limit, timeout_seconds)


def enrich_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return module_enrich_candles(candles)


def fmt_price(value: float) -> str:
    return module_fmt_price(value)


def build_display_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    display_cfg = (config or {}).get("display", {}) if isinstance(config, dict) else {}
    currency = str(display_cfg.get("price_currency", "CNY")).strip().upper() or "CNY"
    if currency not in {"CNY", "USD"}:
        currency = "CNY"
    try:
        usd_cny_rate = float(display_cfg.get("usd_cny_rate", 7.2))
    except Exception:
        usd_cny_rate = 7.2
    if usd_cny_rate <= 0:
        usd_cny_rate = 7.2
    use_live_fx = bool(display_cfg.get("use_live_fx", True))
    try:
        fx_cache_minutes = max(int(display_cfg.get("live_fx_cache_minutes", 60)), 5)
    except Exception:
        fx_cache_minutes = 60
    fx_source = "config"
    fx_updated_at = ""
    if currency == "CNY" and use_live_fx:
        display_state = {}
        if isinstance(config, dict):
            state = config.get("_runtime_state")
            if isinstance(state, dict):
                display_state = state.setdefault("display", {})
        cache = display_state.get("fx_cache", {}) if isinstance(display_state.get("fx_cache"), dict) else {}
        cache_rate = cache.get("usd_cny_rate")
        cache_ts = cache.get("fetched_at_ts")
        now_ts = time.time()
        cache_valid = False
        try:
            cache_rate_value = float(cache_rate)
            cache_ts_value = float(cache_ts)
            cache_valid = cache_rate_value > 0 and (now_ts - cache_ts_value) <= fx_cache_minutes * 60
        except Exception:
            cache_valid = False
        if cache_valid:
            usd_cny_rate = cache_rate_value
            fx_source = str(cache.get("source", "live-cache"))
            fx_updated_at = str(cache.get("fetched_at", ""))
        else:
            try:
                timeout_seconds = int((config or {}).get("runtime", {}).get("http_timeout_seconds", 12))
                live_rate, source = fetch_live_usd_cny_rate(timeout_seconds=timeout_seconds)
                usd_cny_rate = live_rate
                fx_source = source
                fx_updated_at = utc_now_iso()
                display_state["fx_cache"] = {
                    "usd_cny_rate": live_rate,
                    "fetched_at": fx_updated_at,
                    "fetched_at_ts": now_ts,
                    "source": source,
                }
            except Exception:
                try:
                    cache_rate_value = float(cache_rate)
                    if cache_rate_value > 0:
                        usd_cny_rate = cache_rate_value
                        fx_source = str(cache.get("source", "stale-cache"))
                        fx_updated_at = str(cache.get("fetched_at", ""))
                except Exception:
                    fx_source = "config"
    return {
        "price_currency": currency,
        "usd_cny_rate": usd_cny_rate,
        "use_live_fx": use_live_fx,
        "live_fx_cache_minutes": fx_cache_minutes,
        "fx_source": fx_source,
        "fx_updated_at": fx_updated_at,
    }


def attach_display_settings(analysis: dict[str, Any], config: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    if state is not None:
        config["_runtime_state"] = state
    analysis["display"] = build_display_settings(config)
    if state is not None:
        config.pop("_runtime_state", None)
    return analysis


def display_settings_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    display = analysis.get("display", {}) if isinstance(analysis.get("display"), dict) else {}
    currency = str(display.get("price_currency", "CNY")).strip().upper() or "CNY"
    if currency not in {"CNY", "USD"}:
        currency = "CNY"
    try:
        usd_cny_rate = float(display.get("usd_cny_rate", 7.2))
    except Exception:
        usd_cny_rate = 7.2
    if usd_cny_rate <= 0:
        usd_cny_rate = 7.2
    return {
        "price_currency": currency,
        "usd_cny_rate": usd_cny_rate,
    }


def convert_price_for_display(value: float, analysis: dict[str, Any]) -> float:
    settings = display_settings_from_analysis(analysis)
    if settings["price_currency"] == "CNY":
        return value * float(settings["usd_cny_rate"])
    return value


def price_unit_label(locale: str, analysis: dict[str, Any]) -> str:
    settings = display_settings_from_analysis(analysis)
    if settings["price_currency"] == "CNY":
        return tr(locale, "人民币", "CNY")
    return tr(locale, "美元", "USD")


def format_display_price(value: float, analysis: dict[str, Any]) -> str:
    settings = display_settings_from_analysis(analysis)
    symbol = "¥" if settings["price_currency"] == "CNY" else "$"
    return f"{symbol}{fmt_price(convert_price_for_display(value, analysis))}"


def format_display_price_range(low: float, high: float, analysis: dict[str, Any]) -> str:
    return f"{format_display_price(low, analysis)}-{format_display_price(high, analysis)}"


def format_display_delta(value: float, analysis: dict[str, Any]) -> str:
    settings = display_settings_from_analysis(analysis)
    symbol = "¥" if settings["price_currency"] == "CNY" else "$"
    converted = convert_price_for_display(abs(value), analysis)
    sign = "+" if value >= 0 else "-"
    return f"{sign}{symbol}{fmt_price(converted)}"


def clamp(value: float, low: float, high: float) -> float:
    return module_clamp(value, low, high)


def parse_hhmm(value: str) -> int:
    return module_parse_hhmm(value)


def percent_change(a: float, b: float) -> float:
    return module_percent_change(a, b)


def analyze_market(config: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    return module_analyze_market(config, state)


def build_chart_svg(candles: list[dict[str, Any]], current_price: float, output_path: Path, title: str, locale: str = "en") -> Path | None:
    return module_build_chart_svg(candles, current_price, output_path, title, locale)


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
    return module_get_reply_language(config)


def tr(locale: str, zh: str, en: str) -> str:
    return module_tr(locale, zh, en)


def strength_label(score: int, locale: str = "en") -> str:
    return module_strength_label(score, locale)


def localize_signal_name(signal: str, locale: str, *, detailed: bool = True) -> str:
    return module_localize_signal_name(signal, locale, detailed=detailed)


def localize_market_regime(regime: str, locale: str) -> str:
    return module_localize_market_regime(regime, locale)


def localize_reason(reason: str, locale: str) -> str:
    return module_localize_reason(reason, locale)


def localize_reasons(reasons: list[str], locale: str) -> list[str]:
    return module_localize_reasons(reasons, locale)


def build_entry_zone_display(analysis: dict[str, Any], locale: str) -> str:
    if analysis["label"] == "watch":
        return tr(locale, "等待信号确认", "Wait for confirmation")
    plan = analysis.get("entry_plan", {})
    kind = str(plan.get("kind", "reference"))
    if kind == "range":
        low = float(plan.get("low") or analysis["entry_reference"])
        high = float(plan.get("high") or analysis["entry_reference"])
        return format_display_price_range(low, high, analysis)
    if kind == "above":
        trigger = float(plan.get("trigger") or analysis["entry_reference"])
        return f">{format_display_price(trigger, analysis)}"
    return format_display_price(float(analysis["entry_reference"]), analysis)


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
            f"站稳 {format_display_price(trigger, analysis)} 上方后再跟进，避免假突破",
            f"Wait for price to hold above {format_display_price(trigger, analysis)} before following",
        )
    if analysis["primary_signal"] == "reversal":
        return tr(
            locale,
            f"若继续站稳 {format_display_price(trigger, analysis)} 上方，可轻仓试反弹",
            f"Probe only if price holds above {format_display_price(trigger, analysis)}",
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
            f"少量先买: 可以，按 {analysis['position_size_hint']} 分批更稳，失效位看 {format_display_price(analysis['stop_loss'], analysis)}",
            f"Small starter size: yes. Scaling in around {analysis['position_size_hint']} is cleaner, with invalidation near {format_display_price(analysis['stop_loss'], analysis)}.",
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


def infer_trade_recommendation(analysis: dict[str, Any]) -> dict[str, Any]:
    label = str(analysis.get("label", "watch"))
    score = int(analysis.get("score", 0))
    regime = str(analysis.get("market_regime", "Range"))
    position_active = bool(analysis.get("position_active"))
    ml_prediction = analysis.get("ml_prediction") if isinstance(analysis.get("ml_prediction"), dict) else {}
    probabilities = ml_prediction.get("probabilities", {}) if isinstance(ml_prediction, dict) else {}
    up_prob = float(probabilities.get("up", 0.0))
    down_prob = float(probabilities.get("down", 0.0))
    predicted_signal = int(ml_prediction.get("predicted_signal", 0)) if ml_prediction else 0

    if position_active:
        if label == "sell_trigger":
            return {"action": "sell", "confidence": "high", "up_prob": up_prob, "down_prob": down_prob}
        if down_prob >= 0.70 or (label == "watch" and score <= 48):
            return {"action": "sell", "confidence": "high", "up_prob": up_prob, "down_prob": down_prob}
        if down_prob >= 0.58 or regime in {"偏弱", "弱势"}:
            return {"action": "reduce", "confidence": "medium", "up_prob": up_prob, "down_prob": down_prob}
        return {"action": "hold", "confidence": "medium", "up_prob": up_prob, "down_prob": down_prob}

    if label == "sell_trigger":
        return {"action": "sell", "confidence": "high", "up_prob": up_prob, "down_prob": down_prob}
    if label == "buy_trigger" and (score >= 84 or up_prob >= 0.55 or predicted_signal > 0):
        return {"action": "buy", "confidence": "high", "up_prob": up_prob, "down_prob": down_prob}
    if label == "near_buy" and (up_prob >= 0.42 or predicted_signal >= 0):
        return {"action": "probe_buy", "confidence": "medium", "up_prob": up_prob, "down_prob": down_prob}
    if down_prob >= 0.60 or regime in {"偏弱", "弱势"}:
        return {"action": "wait", "confidence": "high", "up_prob": up_prob, "down_prob": down_prob}
    return {"action": "wait", "confidence": "medium", "up_prob": up_prob, "down_prob": down_prob}


def build_trade_recommendation_display(analysis: dict[str, Any], locale: str) -> str:
    recommendation = infer_trade_recommendation(analysis)
    action = recommendation["action"]
    if action == "buy":
        return tr(
            locale,
            f"买入建议: 可以按 {analysis['position_size_hint']} 分批执行，优先在入场区内吸纳，跌破 {format_display_price(analysis['stop_loss'], analysis)} 视为失效。",
            f"Buy recommendation: scale in around {analysis['position_size_hint']} inside the entry zone, and treat a break below {format_display_price(analysis['stop_loss'], analysis)} as invalidation.",
        )
    if action == "probe_buy":
        return tr(
            locale,
            "买入建议: 可先小仓试探，先用建议仓位的一半以内，确认后再补仓。",
            "Buy recommendation: a small probe is reasonable, ideally no more than half of the suggested size before confirmation.",
        )
    if action == "reduce":
        return tr(
            locale,
            "卖出建议: 若你已有仓位，更适合先减仓并上移止损，不建议继续追多。",
            "Sell recommendation: if you already hold a position, trimming and tightening the stop is cleaner than adding more risk.",
        )
    if action == "sell":
        return tr(
            locale,
            "卖出建议: 若你已有仓位，当前更偏向主动离场或明显减仓，优先保护利润与风控。",
            "Sell recommendation: if you already hold a position, the setup now leans toward actively exiting or cutting size to protect capital.",
        )
    if action == "hold":
        return tr(
            locale,
            "持仓建议: 当前更适合继续持有，等新的回踩或确认信号再考虑加仓。",
            "Position recommendation: holding is cleaner for now; wait for a fresh pullback or confirmation before adding.",
        )
    return tr(
        locale,
        "操作建议: 先观望，不追买；等待价格重新进入入场区或结构重新转强。",
        "Action recommendation: stay patient and avoid chasing. Wait for price to re-enter the entry zone or for structure to strengthen again.",
    )


def build_position_summary(state: dict[str, Any], locale: str) -> str:
    position = state.get("position", {})
    if not position.get("active"):
        return tr(locale, "持仓状态: 当前未记录持仓", "Position status: no open position is currently recorded.")
    size_hint = str(position.get("size_hint") or "").strip()
    size_text = tr(locale, f" / 仓位 {size_hint}", f" / size {size_hint}") if size_hint else ""
    display_analysis = {"display": state.get("display", {"price_currency": "CNY", "usd_cny_rate": 7.2})}
    return tr(
        locale,
        f"持仓状态: 已开仓 @ {format_display_price(float(position['entry_price']), display_analysis)}{size_text}",
        f"Position status: open @ {format_display_price(float(position['entry_price']), display_analysis)}{size_text}",
    )


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
                tr(locale, f"现价: {format_display_price(analysis['price'], analysis)}", f"Price: {format_display_price(analysis['price'], analysis)}"),
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
    if "status" in intents:
        lines.append(build_trade_recommendation_display(analysis, locale))
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
        tr(locale, f"现价: {format_display_price(analysis['price'], analysis)}", f"Price: {format_display_price(analysis['price'], analysis)}"),
        tr(locale, f"原始信号: {localize_signal_name(analysis['primary_signal'], locale)}", f"Original signal: {localize_signal_name(analysis['primary_signal'], locale)}"),
        tr(locale, f"入场参考: {format_display_price(entry_reference, analysis)}", f"Reference entry: {format_display_price(entry_reference, analysis)}"),
        tr(locale, f"当前表现: {direction} {pnl_pct:+.2f}%", f"Current performance: {direction} {pnl_pct:+.2f}%"),
        tr(locale, f"止盈1距离: {distance_to_tp1:+.2f}% / {format_display_delta(dollars_to_tp1, analysis)}", f"Distance to TP1: {distance_to_tp1:+.2f}% / {format_display_delta(dollars_to_tp1, analysis)}"),
        tr(locale, f"止损距离: {distance_to_stop:+.2f}% / {format_display_delta(-dollars_to_stop, analysis)}", f"Distance to stop: {distance_to_stop:+.2f}% / {format_display_delta(-dollars_to_stop, analysis)}"),
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
        "sell_trigger": tr(locale, "【ETH提醒】卖出信号", "[ETH Alert] Sell Signal"),
    }
    if analysis["label"] not in title_map:
        action = infer_trade_recommendation(analysis)["action"]
        title_map[analysis["label"]] = {
            "sell": tr(locale, "【ETH提醒】卖出信号", "[ETH Alert] Sell Signal"),
            "reduce": tr(locale, "【ETH提醒】减仓防守", "[ETH Alert] Reduce Risk"),
            "hold": tr(locale, "【ETH提醒】持仓观察", "[ETH Alert] Hold Update"),
        }.get(action, tr(locale, "【ETH提醒】观察更新", "[ETH Alert] Watch Update"))
    lines = [
        title_map[analysis["label"]],
        tr(locale, f"现价: {format_display_price(analysis['price'], analysis)}", f"Price: {format_display_price(analysis['price'], analysis)}"),
        tr(locale, f"信号: {localize_signal_name(analysis['primary_signal'], locale)}", f"Signal: {localize_signal_name(analysis['primary_signal'], locale)}"),
        tr(locale, f"类型: {localize_signal_name(analysis['primary_signal'], locale, detailed=False)}", f"Type: {localize_signal_name(analysis['primary_signal'], locale, detailed=False)}"),
        tr(locale, f"风格: {analysis['strategy_profile']}", f"Profile: {analysis['strategy_profile']}"),
        tr(locale, f"价格单位: {price_unit_label(locale, analysis)}", f"Price unit: {price_unit_label(locale, analysis)}"),
        tr(locale, f"市场状态: {localize_market_regime(analysis['market_regime'], locale)}", f"Market regime: {localize_market_regime(analysis['market_regime'], locale)}"),
        tr(locale, f"强度: {strength_label(int(analysis['score']), locale)} ({analysis['score']}/100)", f"Strength: {strength_label(int(analysis['score']), locale)} ({analysis['score']}/100)"),
        tr(locale, f"入场区间: {build_entry_zone_display(analysis, locale)}", f"Entry zone: {build_entry_zone_display(analysis, locale)}"),
        tr(locale, f"操作: {build_entry_hint_display(analysis, locale)}", f"Plan: {build_entry_hint_display(analysis, locale)}"),
        build_trade_recommendation_display(analysis, locale),
        tr(locale, f"仓位: {analysis['position_size_hint']}", f"Size hint: {analysis['position_size_hint']}"),
        tr(locale, f"止损位: {format_display_price(analysis['stop_loss'], analysis)}", f"Stop: {format_display_price(analysis['stop_loss'], analysis)}"),
        tr(locale, f"止盈1: {format_display_price(analysis['take_profit']['tp1'], analysis)}", f"Take profit 1: {format_display_price(analysis['take_profit']['tp1'], analysis)}"),
        tr(locale, f"止盈2: {format_display_price(analysis['take_profit']['tp2'], analysis)}", f"Take profit 2: {format_display_price(analysis['take_profit']['tp2'], analysis)}"),
        tr(locale, f"盈亏比: 1:{analysis['take_profit']['rr_to_tp1']} / 1:{analysis['take_profit']['rr_to_tp2']}", f"Risk/reward: 1:{analysis['take_profit']['rr_to_tp1']} / 1:{analysis['take_profit']['rr_to_tp2']}"),
    ]
    ml_prediction = analysis.get("ml_prediction")
    if isinstance(ml_prediction, dict):
        up_prob = float(ml_prediction.get("probabilities", {}).get("up", 0.0)) * 100.0
        down_prob = float(ml_prediction.get("probabilities", {}).get("down", 0.0)) * 100.0
        lines.append(
            tr(
                locale,
                f"模型辅助: 看涨 {up_prob:.1f}% / 看跌 {down_prob:.1f}%",
                f"Model assist: up {up_prob:.1f}% / down {down_prob:.1f}%",
            )
        )
    if analysis.get("position_active"):
        lines.append(tr(locale, f"持仓参考: 已开仓 @ {format_display_price(analysis['position_entry_price'], analysis)}", f"Position reference: open @ {format_display_price(analysis['position_entry_price'], analysis)}"))
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


def build_market_structure_summary(analysis: dict[str, Any], locale: str) -> str:
    metrics = analysis.get("metrics", {})
    price = float(analysis.get("price", 0.0))
    ema15_20 = float(metrics.get("15m_ema20", price))
    ema15_50 = float(metrics.get("15m_ema50", price))
    ema1h_20 = float(metrics.get("1h_ema20", price))
    ema1h_50 = float(metrics.get("1h_ema50", price))
    ema4h_20 = float(metrics.get("4h_ema20", price))
    ema4h_50 = float(metrics.get("4h_ema50", price))
    below_short = price < ema15_20 and ema15_20 < ema15_50
    below_mid = price < ema1h_20 and ema1h_20 < ema1h_50
    below_long = price < ema4h_20 and ema4h_20 < ema4h_50
    if below_short and below_mid and below_long:
        return tr(
            locale,
            "多周期均线仍偏空，短中线结构尚未完成真正修复，当前更像弱势整理。",
            "Multiple timeframe averages still lean bearish, so the short- and mid-term structure has not repaired yet and still looks like weak consolidation.",
        )
    if price > ema15_20 and price > ema1h_20 and ema15_20 >= ema15_50:
        return tr(
            locale,
            "短线已经重新站上关键均线，结构开始修复，但仍需要更大级别继续确认。",
            "Price has reclaimed key short-term averages and structure is improving, but higher timeframes still need confirmation.",
        )
    return tr(
        locale,
        "短线处在均线附近反复拉扯，方向并不干净，市场仍在等待新的选择。",
        "Price is still chopping around key moving averages, so direction is not clean and the market is still waiting to choose.",
    )


def build_intraday_context_summary(analysis: dict[str, Any], locale: str) -> str:
    metrics = analysis.get("metrics", {})
    rsi = float(metrics.get("5m_rsi14", 50.0))
    volume_ratio = float(metrics.get("15m_volume_ratio", 1.0))
    macd_improving = bool(analysis.get("conditions", {}).get("macd_improving"))
    if 45 <= rsi <= 55 and volume_ratio < 0.8:
        return tr(
            locale,
            f"短线 RSI 约 {rsi:.1f}，处于中性区，15m 成交量仅为常态的 {volume_ratio:.2f} 倍，说明当前波动缺少放量支持。",
            f"Short-term RSI is around {rsi:.1f}, which is neutral, and 15m volume is only {volume_ratio:.2f}x normal, so the current move lacks volume support.",
        )
    if rsi > 58 and macd_improving:
        return tr(
            locale,
            f"短线 RSI 约 {rsi:.1f} 且 MACD 动能改善，反弹意愿在增强，但还需要量能继续配合。",
            f"Short-term RSI is around {rsi:.1f} and MACD momentum is improving, so rebound intent is building, but volume still needs to confirm.",
        )
    return tr(
        locale,
        f"短线 RSI 约 {rsi:.1f}，量能比约 {volume_ratio:.2f}，节奏上更像震荡试探而非强趋势推进。",
        f"Short-term RSI is around {rsi:.1f} with volume ratio near {volume_ratio:.2f}, which still looks more like probing chop than a strong trend leg.",
    )


def build_model_context_summary(analysis: dict[str, Any], locale: str) -> str:
    ml_prediction = analysis.get("ml_prediction")
    if not isinstance(ml_prediction, dict):
        return tr(locale, "模型侧暂无额外确认，仍以规则结构为主。", "The model has no additional confirmation here, so the rule-based structure remains primary.")
    probabilities = ml_prediction.get("probabilities", {})
    up_prob = float(probabilities.get("up", 0.0)) * 100.0
    hold_prob = float(probabilities.get("hold", 0.0)) * 100.0
    down_prob = float(probabilities.get("down", 0.0)) * 100.0
    return tr(
        locale,
        f"模型判断更偏等待：看涨约 {up_prob:.1f}% / 观望约 {hold_prob:.1f}% / 看跌约 {down_prob:.1f}%，说明当前并不是高把握度进攻点。",
        f"The model still leans toward waiting: up {up_prob:.1f}% / hold {hold_prob:.1f}% / down {down_prob:.1f}%, which means this is not a high-conviction attack zone yet.",
    )


def build_forward_plan_summary(analysis: dict[str, Any], locale: str) -> str:
    levels = build_key_levels(analysis)
    entry_plan = analysis.get("entry_plan", {})
    trigger = entry_plan.get("trigger") or analysis.get("entry_reference")
    if infer_trade_recommendation(analysis)["action"] in {"buy", "probe_buy"}:
        return tr(
            locale,
            f"操作预案: 若价格维持在 {build_entry_zone_display(analysis, locale)} 附近，可按 {analysis['position_size_hint']} 分批参与；跌破 {format_display_price(analysis['stop_loss'], analysis)} 则视为失效。",
            f"Plan: if price holds around {build_entry_zone_display(analysis, locale)}, scaling in with {analysis['position_size_hint']} makes sense; a break below {format_display_price(analysis['stop_loss'], analysis)} invalidates the setup.",
        )
    return tr(
        locale,
        f"操作预案: 先等价格重新站回 {format_display_price(float(trigger), analysis)} 一带并确认，或放量突破 {format_display_price(levels['resistance'], analysis)} 后再评估；若跌破 {format_display_price(levels['support'], analysis)}，继续按弱势看待。",
        f"Plan: wait for price to reclaim roughly {format_display_price(float(trigger), analysis)} with confirmation, or reassess after a volume-backed break above {format_display_price(levels['resistance'], analysis)}; if price loses {format_display_price(levels['support'], analysis)}, keep treating the market as weak.",
    )


def build_risk_watch_summary(analysis: dict[str, Any], locale: str) -> str:
    localized_reasons = localize_reasons(analysis.get("reasons", [])[:1], locale)
    default_text = tr(locale, "当前最大风险仍是结构不够清晰时贸然追价。", "The main risk is still forcing entries before structure is clean enough.")
    reason = localized_reasons[0] if localized_reasons else default_text
    return tr(locale, f"风险点: {reason}", f"Risk watch: {reason}")


def display_metrics_snapshot(analysis: dict[str, Any]) -> dict[str, Any]:
    metrics = analysis.get("metrics", {})
    output: dict[str, Any] = {}
    for key, value in metrics.items():
        if not isinstance(value, (int, float)):
            output[key] = value
            continue
        if any(token in key for token in ["ema", "breakout_level"]):
            output[key] = round(convert_price_for_display(float(value), analysis), 2)
        else:
            output[key] = value
    return output


def build_daily_summary_payload(analysis: dict[str, Any], state: dict[str, Any], locale: str) -> dict[str, Any]:
    bias, confidence = infer_forecast_bias(analysis)
    levels = build_key_levels(analysis)
    recommendation = infer_trade_recommendation(analysis)
    return {
        "locale": locale,
        "symbol": analysis["symbol"],
        "generated_at": analysis["generated_at"],
        "currency": price_unit_label(locale, analysis),
        "price": round(convert_price_for_display(float(analysis["price"]), analysis), 2),
        "price_display": format_display_price(float(analysis["price"]), analysis),
        "label": analysis["label"],
        "signal": analysis["primary_signal"],
        "signal_name": localize_signal_name(analysis["primary_signal"], locale),
        "market_regime": localize_market_regime(analysis["market_regime"], locale),
        "score": int(analysis["score"]),
        "entry_zone": build_entry_zone_display(analysis, locale),
        "entry_hint": build_entry_hint_display(analysis, locale),
        "position_size_hint": analysis["position_size_hint"],
        "stop_loss": round(convert_price_for_display(float(analysis["stop_loss"]), analysis), 2),
        "stop_loss_display": format_display_price(float(analysis["stop_loss"]), analysis),
        "take_profit_1": round(convert_price_for_display(float(analysis["take_profit"]["tp1"]), analysis), 2),
        "take_profit_1_display": format_display_price(float(analysis["take_profit"]["tp1"]), analysis),
        "take_profit_2": round(convert_price_for_display(float(analysis["take_profit"]["tp2"]), analysis), 2),
        "take_profit_2_display": format_display_price(float(analysis["take_profit"]["tp2"]), analysis),
        "forecast_bias": bias,
        "forecast_confidence": confidence,
        "action_recommendation": recommendation["action"],
        "action_recommendation_text": build_trade_recommendation_display(analysis, locale),
        "support": round(convert_price_for_display(levels["support"], analysis), 2),
        "support_display": format_display_price(levels["support"], analysis),
        "resistance": round(convert_price_for_display(levels["resistance"], analysis), 2),
        "resistance_display": format_display_price(levels["resistance"], analysis),
        "market_structure_text": build_market_structure_summary(analysis, locale),
        "intraday_context_text": build_intraday_context_summary(analysis, locale),
        "model_context_text": build_model_context_summary(analysis, locale),
        "forward_plan_text": build_forward_plan_summary(analysis, locale),
        "risk_watch_text": build_risk_watch_summary(analysis, locale),
        "position_active": bool(analysis.get("position_active")),
        "position_entry_price": round(convert_price_for_display(float(analysis.get("position_entry_price", analysis["entry_reference"])), analysis), 2),
        "position_entry_price_display": format_display_price(float(analysis.get("position_entry_price", analysis["entry_reference"])), analysis),
        "reasons": localize_reasons(analysis.get("reasons", [])[:4], locale),
        "metrics": display_metrics_snapshot(analysis),
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
            "Keep it under 1200 characters.",
            "Write like a real market note, not a shallow summary.",
            "Be concrete, not generic. Mention exact levels, market structure, and a clear action bias.",
            "All prices in the structured data are already converted to the display currency. Keep using that currency consistently.",
            "Use 6 short sections in this order:",
            "1. Market stance: one-line conclusion.",
            "2. Structure: explain trend condition across short/mid/higher timeframes.",
            "3. Intraday context: explain RSI / volume / momentum condition.",
            "4. Key levels and next 24h path.",
            "5. Action plan: buy now, probe small, wait, reduce, hold, or sell, with specific levels.",
            "6. Risk watch: one main risk.",
            "If the setup is weak, say so clearly.",
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
    action_text = build_trade_recommendation_display(analysis, locale)
    lines = [
        tr(locale, "【ETH日报】市场评价与预测", "[ETH Daily] Market Review and Forecast"),
        tr(
            locale,
            f"市场结论: 当前 ETH 处于 {localize_market_regime(analysis['market_regime'], locale)}，主信号 {localize_signal_name(analysis['primary_signal'], locale)}，强度 {strength_label(int(analysis['score']), locale)}。",
            f"Market stance: ETH is in a {localize_market_regime(analysis['market_regime'], locale)} regime, with {localize_signal_name(analysis['primary_signal'], locale)} as the primary setup and {strength_label(int(analysis['score']), locale)} strength.",
        ),
        build_market_structure_summary(analysis, locale),
        build_intraday_context_summary(analysis, locale),
        tr(
            locale,
            f"市场预测: 未来 24 小时更偏 {bias_text}，信心 {confidence_text}。",
            f"Forecast: the next 24 hours lean {bias_text} with {confidence_text} confidence.",
        ),
        tr(
            locale,
            f"关键位: 支撑约 {format_display_price(levels['support'], analysis)}，压力约 {format_display_price(levels['resistance'], analysis)}。",
            f"Key levels: support near {format_display_price(levels['support'], analysis)}, resistance near {format_display_price(levels['resistance'], analysis)}.",
        ),
        build_model_context_summary(analysis, locale),
        tr(locale, f"位置判断: {build_entry_distance_summary(analysis, locale)}", f"Positioning: {build_entry_distance_summary(analysis, locale)}"),
        action_text,
        build_forward_plan_summary(analysis, locale),
        build_risk_watch_summary(analysis, locale),
        tr(locale, "仅供参考，不构成投资建议。", "For reference only. Not financial advice."),
    ]
    if position_is_open(state):
        lines.insert(
            4,
            tr(
                locale,
                f"持仓参考: 你的记录开仓价为 {format_display_price(position_entry_reference(state, analysis['entry_reference']), analysis)}。",
                f"Position reference: your recorded entry is {format_display_price(position_entry_reference(state, analysis['entry_reference']), analysis)}.",
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


def extract_message_send_metadata(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        return {}
    message_payload = payload.get("payload", {}) if isinstance(payload, dict) else {}
    delivery_result = message_payload.get("result", {}) if isinstance(message_payload, dict) else {}
    return {
        "target": str(message_payload.get("to") or ""),
        "message_id": str(delivery_result.get("messageId") or ""),
    }


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
    daily_state = state.setdefault("daily_summary", {})
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
    send_metadata = extract_message_send_metadata(result)
    audit_entry = {
        "ts": utc_now_iso(),
        "send_key": send_key,
        "status": "sent" if result.returncode == 0 else "failed",
        "channel": str(config.get("notification", {}).get("channel", "")),
        "target": send_metadata.get("target") or str(config.get("notification", {}).get("target", "")),
        "locale": locale,
        "used_llm": bool(llm_text),
        "llm_reason": llm_reason,
        "usage": usage,
        "attach_chart": bool(media_path),
        "message_id": send_metadata.get("message_id", ""),
        "detail": (
            llm_reason
            if result.returncode == 0
            else (result.stderr.strip() or result.stdout.strip() or "daily summary send failed")
        )[:280],
    }
    audit_history = [item for item in daily_state.get("audit_history", []) if isinstance(item, dict)]
    audit_history.append(audit_entry)
    daily_state["audit_history"] = audit_history[-30:]
    daily_state["last_audit"] = audit_entry
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr, flush=True)
        return False, "daily summary send failed"
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


def compact_alert_history(state: dict[str, Any]) -> None:
    module_compact_alert_history(state)


def position_is_open(state: dict[str, Any]) -> bool:
    return module_position_is_open(state)


def position_entry_reference(state: dict[str, Any], fallback: float) -> float:
    return module_position_entry_reference(state, fallback)


def start_tracking(state: dict[str, Any], analysis: dict[str, Any], config: dict[str, Any]) -> None:
    module_start_tracking(state, analysis, config)


def clear_tracking_if_expired(state: dict[str, Any]) -> None:
    module_clear_tracking_if_expired(state)


def should_send_alert(analysis: dict[str, Any], state: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    return module_should_send_alert(
        analysis,
        state,
        config,
        in_quiet_hours=in_quiet_hours(config),
        in_active_windows=in_active_windows(config),
    )


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
    print(json.dumps(display_metrics_snapshot(analysis), indent=2, ensure_ascii=False), flush=True)
    print("", flush=True)
    print(tr(locale, "各信号评分:", "Signal scores:"), flush=True)
    signal_scores = {name: payload["score"] for name, payload in analysis["signals"].items()}
    print(json.dumps(signal_scores, indent=2, ensure_ascii=False), flush=True)


def sync_display_state(state: dict[str, Any], analysis: dict[str, Any]) -> None:
    display_state = state.setdefault("display", {})
    if not isinstance(display_state, dict):
        display_state = {}
        state["display"] = display_state
    fx_cache = display_state.get("fx_cache", {}) if isinstance(display_state.get("fx_cache"), dict) else {}
    display_state.clear()
    display_state.update(analysis.get("display", {}))
    if fx_cache:
        display_state["fx_cache"] = fx_cache


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
        analysis = attach_display_settings(maybe_enrich_analysis_with_ml(analyze_market(config, state), config), config, state)
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

    sync_display_state(state, analysis)
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
        analysis = attach_display_settings(maybe_enrich_analysis_with_ml(analyze_market(config, state), config), config, state)
    except Exception as exc:
        state["last_analysis"] = {
            "generated_at": utc_now_iso(),
            "status": "error",
            "error": str(exc),
        }
        save_json_file(state_path, state)
        print(f"ETH watcher error: {exc}", file=sys.stderr, flush=True)
        return 1

    sync_display_state(state, analysis)
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

    download_history = subparsers.add_parser("download-history", help="Download OHLCV data with CCXT")
    download_history.add_argument("--exchange", default="binance")
    download_history.add_argument("--symbol", default="ETH/USDT")
    download_history.add_argument("--timeframe", default="15m")
    download_history.add_argument("--limit", type=int, default=1000)
    download_history.add_argument("--since-ms", default="")
    download_history.add_argument("--output", default="data/binance_ethusdt_15m.csv")

    build_features = subparsers.add_parser("build-features", help="Build Pandas feature dataset")
    build_features.add_argument("--input", required=True)
    build_features.add_argument("--output", default="data/features_ethusdt_15m.csv")
    build_features.add_argument("--horizon", type=int, default=4)
    build_features.add_argument("--threshold-pct", type=float, default=0.35)

    train_model = subparsers.add_parser("train-model", help="Train XGBoost signal model")
    train_model.add_argument("--features", required=True)
    train_model.add_argument("--model-output", default="models/xgboost_eth_signal.json")
    train_model.add_argument("--metadata-output", default="models/xgboost_eth_signal.meta.json")
    train_model.add_argument("--max-depth", type=int, default=4)
    train_model.add_argument("--learning-rate", type=float, default=0.05)
    train_model.add_argument("--n-estimators", type=int, default=300)

    backtest_parser = subparsers.add_parser("backtest", help="Run Backtrader backtest with trained model")
    backtest_parser.add_argument("--input", required=True)
    backtest_parser.add_argument("--model-path", default="models/xgboost_eth_signal.json")
    backtest_parser.add_argument("--metadata-path", default="models/xgboost_eth_signal.meta.json")
    backtest_parser.add_argument("--cash", type=float, default=None)
    backtest_parser.add_argument("--commission", type=float, default=None)
    backtest_parser.add_argument("--risk-fraction", type=float, default=None)
    backtest_parser.add_argument("--stop-loss-atr", type=float, default=None)
    backtest_parser.add_argument("--take-profit-rr", type=float, default=None)
    backtest_parser.add_argument("--entry-prob-threshold", type=float, default=None)
    backtest_parser.add_argument("--exit-prob-threshold", type=float, default=None)
    backtest_parser.add_argument("--min-hold-bars", type=int, default=None)
    backtest_parser.add_argument("--report-dir", default="")
    backtest_parser.add_argument("--no-report", action="store_true")

    sweep_parser = subparsers.add_parser("sweep-backtest", help="Run parameter sweep across backtest settings")
    sweep_parser.add_argument("--input", required=True)
    sweep_parser.add_argument("--model-path", default="models/xgboost_eth_signal.json")
    sweep_parser.add_argument("--metadata-path", default="models/xgboost_eth_signal.meta.json")
    sweep_parser.add_argument("--cash", type=float, default=None)
    sweep_parser.add_argument("--commission", type=float, default=None)
    sweep_parser.add_argument("--risk-fraction", type=float, default=None)
    sweep_parser.add_argument("--take-profit-rr", type=float, default=None)
    sweep_parser.add_argument("--min-hold-bars", type=int, default=None)
    sweep_parser.add_argument("--entry-prob-thresholds", default="")
    sweep_parser.add_argument("--exit-prob-thresholds", default="")
    sweep_parser.add_argument("--stop-loss-atrs", default="")
    sweep_parser.add_argument("--top", type=int, default=None)
    sweep_parser.add_argument("--report-dir", default="")
    sweep_parser.add_argument("--no-report", action="store_true")
    sweep_parser.add_argument("--no-apply-best-to-config", action="store_true")

    send_test = subparsers.add_parser("send-test", help="Send a test iMessage payload")
    send_test.add_argument("--dry-run", action="store_true", help="Dry-run only")
    send_test.add_argument("--scenario", choices=["buy", "sell"], default="buy", help="Sample alert scenario")

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
    if args.command == "download-history":
        return run_download_history(args)
    if args.command == "build-features":
        return run_build_features(args)
    if args.command == "train-model":
        return run_train_model_cmd(args)
    if args.command == "backtest":
        return run_backtest_cmd(args)
    if args.command == "sweep-backtest":
        return run_sweep_backtest_cmd(args)
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
        if args.scenario == "sell":
            sample_analysis = {
                "label": "sell_trigger",
                "price": 1986.40,
                "primary_signal": "watch",
                "strategy_profile": config["strategy_profile"],
                "market_regime": "弱势",
                "score": 34,
                "entry_reference": 2008.8,
                "entry_plan": {
                    "kind": "reference",
                    "low": None,
                    "high": None,
                    "trigger": None,
                    "reference": 2008.8,
                },
                "position_size_hint": "8%-12%",
                "stop_loss": 1991.80,
                "take_profit": {
                    "tp1": 1978.2,
                    "tp2": 1970.4,
                    "rr_to_tp1": 1.5,
                    "rr_to_tp2": 2.3,
                },
                "position_active": True,
                "position_entry_price": 2008.80,
                "ml_prediction": {
                    "predicted_class": 0,
                    "predicted_signal": -1,
                    "probabilities": {
                        "down": 0.78,
                        "hold": 0.18,
                        "up": 0.04,
                    },
                },
                "reasons": [
                    "1h 与 15m 均线重新转弱，价格跌回关键均线下方",
                    "反弹没有放量延续，短线结构转差",
                    "模型看空概率明显抬升，优先保护已有利润",
                ],
                "display": build_display_settings(config),
            }
        else:
            sample_analysis = {
                "label": "buy_trigger",
                "price": 1992.80,
                "primary_signal": "pullback",
                "strategy_profile": config["strategy_profile"],
                "market_regime": "偏多",
                "score": 88,
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
                "ml_prediction": {
                    "predicted_class": 2,
                    "predicted_signal": 1,
                    "probabilities": {
                        "down": 0.07,
                        "hold": 0.21,
                        "up": 0.72,
                    },
                },
                "reasons": [
                    "1h 与 15m 均线同向，趋势配合",
                    "价格回到 EMA20 附近，追高风险较低",
                    "RSI 回升，短线动能在恢复",
                ],
                "display": build_display_settings(config),
            }
        payload = build_message(sample_analysis, locale)
        media_path: Path | None = None
        if should_attach_chart(config) and args.scenario != "sell":
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
