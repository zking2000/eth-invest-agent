from __future__ import annotations

from typing import Any

from eth_agent.data.binance import fetch_klines, fetch_price
from eth_agent.features.indicators import average, ema_slope_up, enrich_candles, fmt_price, percent_change
from eth_agent.risk.management import position_entry_reference, position_is_open
from eth_agent.utils.time import utc_now_iso


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
    regime_map = {4: "强多头", 3: "偏多", 2: "震荡", 1: "偏弱", 0: "弱势"}

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
