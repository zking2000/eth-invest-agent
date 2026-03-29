from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

import pandas as pd

from eth_agent.features.pipeline import build_feature_frame
from eth_agent.models.xgboost_model import XGBoostSignalModel

try:
    import backtrader as bt  # type: ignore
except Exception:
    bt = None


@dataclass
class BacktestConfig:
    initial_cash: float = 10000.0
    commission: float = 0.001
    risk_fraction: float = 0.01
    stop_loss_atr: float = 1.3
    take_profit_rr: float = 1.5
    entry_prob_threshold: float = 0.34
    exit_prob_threshold: float = 0.42
    min_hold_bars: int = 2


if bt is not None:

    class _SignalData(bt.feeds.PandasData):
        lines = ("signal", "atr14", "prob_down", "prob_hold", "prob_up")
        params = (
            ("datetime", None),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", -1),
            ("signal", "predicted_signal"),
            ("atr14", "atr14"),
            ("prob_down", "prob_down"),
            ("prob_hold", "prob_hold"),
            ("prob_up", "prob_up"),
        )


    class MLSignalStrategy(bt.Strategy):
        params = dict(
            risk_fraction=0.01,
            stop_loss_atr=1.3,
            take_profit_rr=1.5,
            entry_prob_threshold=0.34,
            exit_prob_threshold=0.42,
            min_hold_bars=2,
        )

        def __init__(self) -> None:
            self.order = None
            self.stop_price = None
            self.target_price = None
            self.pending_exit_reason = ""
            self.entry_bar = 0
            self.active_trade: dict[str, Any] | None = None
            self.trade_log: list[dict[str, Any]] = []
            self.equity_curve: list[dict[str, Any]] = []
            self.order_status_counts: dict[str, int] = {}
            self.entry_ready_count = 0
            self.exit_ready_count = 0

        def _bar_dt(self) -> str:
            return bt.num2date(self.datas[0].datetime[0]).isoformat()

        def next(self) -> None:
            self.equity_curve.append(
                {
                    "datetime": self._bar_dt(),
                    "equity": float(self.broker.getvalue()),
                    "close": float(self.datas[0].close[0]),
                }
            )
            if self.order:
                return
            signal = int(self.datas[0].signal[0])
            close = float(self.datas[0].close[0])
            atr = max(float(self.datas[0].atr14[0]), 1e-6)
            prob_up = float(self.datas[0].prob_up[0])
            prob_down = float(self.datas[0].prob_down[0])
            bars_held = max(len(self) - self.entry_bar, 0) if self.position else 0

            entry_ready = signal > 0 or (prob_up >= float(self.p.entry_prob_threshold) and prob_up > prob_down)
            if not self.position and entry_ready:
                self.entry_ready_count += 1
                cash = self.broker.getcash()
                risk_budget = cash * float(self.p.risk_fraction)
                stop_distance = atr * float(self.p.stop_loss_atr)
                risk_units = risk_budget / stop_distance if stop_distance > 0 else 0.0
                commission_buffer = 1.0 + float(self.broker.getcommissioninfo(self.datas[0]).p.commission)
                max_affordable_units = (cash * 0.98) / (close * commission_buffer) if close > 0 else 0.0
                size = max(min(risk_units, max_affordable_units), 0.0)
                if size <= 0:
                    return
                self.stop_price = close - stop_distance
                self.target_price = close + stop_distance * float(self.p.take_profit_rr)
                self.entry_bar = len(self)
                self.active_trade = {
                    "entry_signal": signal,
                    "planned_entry_dt": self._bar_dt(),
                    "planned_entry_price": close,
                    "size": float(size),
                    "stop_price": float(self.stop_price),
                    "target_price": float(self.target_price),
                }
                self.order = self.buy(size=size)
            elif self.position:
                exit_reason = ""
                if close <= float(self.stop_price or 0):
                    exit_reason = "stop_loss"
                elif close >= float(self.target_price or 0):
                    exit_reason = "take_profit"
                elif signal < 0 and bars_held >= int(self.p.min_hold_bars):
                    exit_reason = "signal_flip"
                elif prob_down >= float(self.p.exit_prob_threshold) and prob_down > prob_up and bars_held >= int(self.p.min_hold_bars):
                    exit_reason = "probability_flip"
                if exit_reason:
                    self.exit_ready_count += 1
                    self.pending_exit_reason = exit_reason
                    self.order = self.close()

        def notify_order(self, order: Any) -> None:
            status_name = getattr(order, "getstatusname", lambda: str(order.status))()
            self.order_status_counts[status_name] = self.order_status_counts.get(status_name, 0) + 1
            if order.status == order.Completed:
                if order.isbuy() and self.active_trade is not None:
                    self.active_trade["entry_dt"] = self._bar_dt()
                    self.active_trade["entry_price"] = float(order.executed.price)
                    self.active_trade["entry_value"] = float(order.executed.value)
                    self.active_trade["entry_commission"] = float(order.executed.comm)
                elif order.issell() and self.active_trade is not None:
                    self.active_trade["exit_dt"] = self._bar_dt()
                    self.active_trade["exit_price"] = float(order.executed.price)
                    self.active_trade["exit_commission"] = float(order.executed.comm)
                    self.active_trade["exit_reason"] = self.pending_exit_reason or "close"
                    self.active_trade["bars_held"] = max(len(self) - self.entry_bar, 0)
            if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
                self.order = None

        def notify_trade(self, trade: Any) -> None:
            if not trade.isclosed or self.active_trade is None:
                return
            entry_price = float(self.active_trade.get("entry_price") or self.active_trade.get("planned_entry_price") or 0.0)
            exit_price = float(self.active_trade.get("exit_price") or 0.0)
            return_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
            record = dict(self.active_trade)
            record.update(
                {
                    "pnl": float(trade.pnl),
                    "pnl_after_commission": float(trade.pnlcomm),
                    "return_pct": return_pct,
                }
            )
            self.trade_log.append(record)
            self.active_trade = None
            self.pending_exit_reason = ""


def run_backtest(frame: pd.DataFrame, model: XGBoostSignalModel, config: BacktestConfig | None = None) -> dict[str, Any]:
    if bt is None:
        raise RuntimeError("backtrader is not installed. Install dependencies before running backtests.")
    cfg = config or BacktestConfig()
    feature_frame = build_feature_frame(frame)
    scored = model.score_frame(feature_frame)
    data = scored[
        ["timestamp", "open", "high", "low", "close", "volume", "atr14", "predicted_signal", "prob_down", "prob_hold", "prob_up"]
    ].set_index("timestamp")
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cfg.initial_cash)
    cerebro.broker.setcommission(commission=cfg.commission)
    cerebro.addstrategy(
        MLSignalStrategy,
        risk_fraction=cfg.risk_fraction,
        stop_loss_atr=cfg.stop_loss_atr,
        take_profit_rr=cfg.take_profit_rr,
        entry_prob_threshold=cfg.entry_prob_threshold,
        exit_prob_threshold=cfg.exit_prob_threshold,
        min_hold_bars=cfg.min_hold_bars,
    )
    cerebro.adddata(_SignalData(dataname=data))
    start_value = cerebro.broker.getvalue()
    strategies = cerebro.run()
    end_value = cerebro.broker.getvalue()
    strategy = strategies[0]
    equity_frame = pd.DataFrame(strategy.equity_curve)
    if equity_frame.empty:
        equity_frame = pd.DataFrame([{"datetime": None, "equity": start_value, "close": None}])
    equity_series = equity_frame["equity"].astype(float)
    equity_frame["datetime"] = pd.to_datetime(equity_frame["datetime"], utc=True, errors="coerce")
    running_peak = equity_series.cummax()
    drawdown_series = (equity_series / running_peak - 1.0) * 100.0
    max_drawdown_pct = abs(float(drawdown_series.min())) if not drawdown_series.empty else 0.0
    return_series = equity_series.pct_change().fillna(0.0)
    annualization_factor = 365 * 24 * 4
    volatility_pct = float(return_series.std() * sqrt(annualization_factor) * 100.0) if len(return_series) > 1 else 0.0
    sharpe_ratio = 0.0
    std = float(return_series.std())
    if std > 0:
        sharpe_ratio = float((return_series.mean() / std) * sqrt(annualization_factor))
    trades = []
    for trade in strategy.trade_log:
        normalized = {
            "entry_dt": trade.get("entry_dt") or trade.get("planned_entry_dt"),
            "exit_dt": trade.get("exit_dt"),
            "entry_price": round(float(trade.get("entry_price") or trade.get("planned_entry_price") or 0.0), 4),
            "exit_price": round(float(trade.get("exit_price") or 0.0), 4),
            "size": round(float(trade.get("size") or 0.0), 6),
            "stop_price": round(float(trade.get("stop_price") or 0.0), 4),
            "target_price": round(float(trade.get("target_price") or 0.0), 4),
            "bars_held": int(trade.get("bars_held") or 0),
            "exit_reason": str(trade.get("exit_reason") or ""),
            "pnl": round(float(trade.get("pnl") or 0.0), 4),
            "pnl_after_commission": round(float(trade.get("pnl_after_commission") or 0.0), 4),
            "return_pct": round(float(trade.get("return_pct") or 0.0), 4),
        }
        trades.append(normalized)
    total_trades = len(trades)
    winning_trades = [trade for trade in trades if trade["pnl_after_commission"] > 0]
    losing_trades = [trade for trade in trades if trade["pnl_after_commission"] < 0]
    win_rate_pct = (len(winning_trades) / total_trades * 100.0) if total_trades else 0.0
    avg_trade_return_pct = (
        sum(float(trade["return_pct"]) for trade in trades) / total_trades if total_trades else 0.0
    )
    best_trade = max(trades, key=lambda trade: trade["pnl_after_commission"], default=None)
    worst_trade = min(trades, key=lambda trade: trade["pnl_after_commission"], default=None)
    reason_distribution: dict[str, int] = {}
    for trade in trades:
        reason = str(trade["exit_reason"] or "unknown")
        reason_distribution[reason] = reason_distribution.get(reason, 0) + 1
    monthly_returns: list[dict[str, Any]] = []
    monthly_frame = equity_frame.dropna(subset=["datetime"]).copy()
    if not monthly_frame.empty:
        monthly_frame = monthly_frame.set_index("datetime")
        monthly_equity = monthly_frame["equity"].resample("ME").last().dropna()
        monthly_pct = monthly_equity.pct_change().fillna(0.0) * 100.0
        monthly_returns = [
            {"month": idx.strftime("%Y-%m"), "return_pct": round(float(value), 4)}
            for idx, value in monthly_pct.items()
        ]
    return {
        "starting_value": round(start_value, 2),
        "ending_value": round(end_value, 2),
        "return_pct": round(((end_value - start_value) / start_value) * 100.0, 2) if start_value else 0.0,
        "rows": int(len(data)),
        "total_trades": total_trades,
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate_pct": round(win_rate_pct, 2),
        "average_trade_return_pct": round(avg_trade_return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "volatility_pct": round(volatility_pct, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "equity_curve_summary": {
            "points": int(len(equity_frame)),
            "peak_value": round(float(equity_series.max()), 2) if not equity_series.empty else round(start_value, 2),
            "trough_value": round(float(equity_series.min()), 2) if not equity_series.empty else round(start_value, 2),
        },
        "equity_curve": [
            {
                "datetime": item["datetime"].isoformat() if hasattr(item["datetime"], "isoformat") else str(item["datetime"]),
                "equity": round(float(item["equity"]), 4),
                "close": round(float(item["close"]), 4) if item["close"] is not None else None,
            }
            for item in equity_frame.to_dict(orient="records")
        ],
        "monthly_returns": monthly_returns,
        "exit_reason_distribution": reason_distribution,
        "diagnostics": {
            "entry_ready_count": int(strategy.entry_ready_count),
            "exit_ready_count": int(strategy.exit_ready_count),
            "order_status_counts": strategy.order_status_counts,
        },
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "trades": trades,
    }
