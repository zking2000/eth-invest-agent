from __future__ import annotations

from typing import Any


def tr(locale: str, zh: str, en: str) -> str:
    return zh if locale == "zh" else en


def get_reply_language(config: dict[str, Any]) -> str:
    value = str(config.get("notification", {}).get("reply_language", "en")).strip().lower()
    return "zh" if value.startswith("zh") else "en"


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
    return regime if locale == "zh" else mapping.get(regime, regime)


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
