# ETH Watcher

`ETH Watcher` is a local ETH monitoring script built around `OpenClaw`.

[English](./README.md) | [简体中文](./README.zh-CN.md)

When updating project documentation, keep `README.md` and `README.zh-CN.md`
in sync.

It is designed to:

- watch `ETHUSDT` with low ongoing cost
- score rule-based buy setups locally
- send alerts to `iMessage`
- support simple inbound chat queries
- switch between faster or more conservative trading profiles

## What It Does

The watcher pulls public Binance market data, calculates technical indicators,
and evaluates three setup types:

- `pullback`: trend pullback into moving-average support
- `breakout`: breakout above recent `15m` highs with volume confirmation
- `reversal`: oversold rebound with fast reclaim behavior

Timeframes used:

- `5m`
- `15m`
- `1h`
- `4h`

Indicators used:

- `EMA20 / EMA50`
- `RSI14`
- `ATR14`
- `MACD histogram`

## How You Can Interact With It

There are two main ways to use the watcher: local CLI commands and iMessage chat.

### Before You Start

The example config in this repository is intentionally safe for open-source use:

- alerts are disabled by default
- the iMessage target is blank
- reply language defaults to English

Before expecting any outbound alert or chat reply, update `config.json` with
your own destination:

```json
{
  "notification": {
    "enabled": true,
    "target": "your-imessage-handle",
    "reply_language": "en"
  }
}
```

If `notification.target` is empty, the watcher has nowhere to send alerts or
chat replies.

### 1. Interact Locally From The Terminal

These commands let you query the watcher directly without using iMessage:

```bash
python3 ./scripts/eth_watcher.py snapshot
python3 ./scripts/eth_watcher.py chat-query --message "Can I buy now?"
python3 ./scripts/eth_watcher.py chat-query --message "How far is price from the entry?"
python3 ./scripts/eth_watcher.py position-status
```

What each command does:

- `snapshot`: prints the current market readout and signal scores
- `chat-query --message "..."`
: asks the same question engine used by chat replies
- `position-status`: shows the currently recorded position, if any

### 2. Passive Alerts

Run the watcher in daemon mode and let it push messages when a setup matches:

```bash
python3 ./scripts/eth_watcher.py daemon
```

Alert messages can include:

- setup type
- entry zone
- stop loss
- take profit levels
- risk/reward
- position size hint
- strength score

The daemon can also send a scheduled daily market review, even when no buy setup
is triggered. By default, the repository example is configured to send one daily
summary at `09:00`.

### 3. Ask It Questions In iMessage

The project includes an OpenClaw hook that lets you message the watcher and get
a direct reply instead of waiting for an alert.

Enable the hook:

```bash
openclaw hooks enable eth-chat
openclaw gateway restart
```

Then send a message to the same iMessage target configured in `config.json`.

Examples:

- `ETH`
- `Can I buy now?`
- `How far is price from the entry?`
- `Can I start with a small size?`
- `How is it performing now?`
- `Position status`
- `Why this view?`
- `HELP`

The parser also supports Chinese prompts if you prefer to ask in Chinese.

Typical reply topics:

- whether the current setup is `watch`, `near buy`, or `buy trigger`
- how far price is from the suggested entry
- whether starting with a small position makes sense
- current performance versus the latest tracked reference
- recorded position status
- short reasons behind the current view

### 3a. How To Confirm The Hook Is Registered

Check whether OpenClaw can see the hook:

```bash
openclaw hooks list --verbose
```

You should see an entry similar to:

```text
eth-chat   ✓ ready
```

To confirm the gateway actually registered it at runtime:

```bash
rg "Registered hook: eth-chat|eth-chat" ~/.openclaw/logs/gateway.log
```

You should see a line similar to:

```text
Registered hook: eth-chat -> message:preprocessed
```

If it does not appear yet, restart the gateway and check again:

```bash
openclaw gateway restart
openclaw hooks list --verbose
```

### 4. Record A Position For Better Follow-Ups

If you want the watcher to talk about your real trade rather than only its
model entry price:

```bash
python3 ./scripts/eth_watcher.py position-open --entry-price 1988.5 --size "10%"
python3 ./scripts/eth_watcher.py position-status
python3 ./scripts/eth_watcher.py position-close
```

Once a position is recorded, follow-up messages can refer to your actual entry
instead of only the signal reference price.

## Reply Language

You can choose whether alerts and chat replies are generated in English or
Chinese.

In `config.json`:

```json
{
  "notification": {
    "reply_language": "en"
  }
}
```

Supported values:

- `"en"`: English replies
- `"zh"`: Chinese replies

This affects:

- alert messages
- follow-up tracking messages
- chat replies
- text snapshots printed by the script

## Quick Start

```bash
cd /path/to/eth-invest-agent
python3 ./scripts/eth_watcher.py snapshot
python3 ./scripts/eth_watcher.py run-once --send --dry-run
python3 ./scripts/eth_watcher.py daemon
python3 ./scripts/eth_watcher.py send-test --dry-run
python3 ./scripts/eth_watcher.py chat-query --message "Can I buy now?"
python3 ./scripts/eth_watcher.py position-open --entry-price 1988.5 --size "10%"
python3 ./scripts/eth_watcher.py position-status
python3 ./scripts/eth_watcher.py position-close
```

You can also set the project root explicitly:

```bash
export ETH_AGENT_HOME="/path/to/eth-invest-agent"
python3 "$ETH_AGENT_HOME/scripts/eth_watcher.py" snapshot
```

## Configuration

Important fields in `config.json`:

- `symbol`: market symbol, default `ETHUSDT`
- `strategy_profile`: `scalp`, `balanced`, or `swing`
- `notification.enabled`: enable or disable outbound alerts
- `notification.target`: your iMessage handle
- `notification.reply_language`: `en` or `zh`
- `notification.cooldown_minutes`: minimum time between alerts
- `notification.max_alerts_per_day`: daily alert cap
- `notification.min_score_to_alert`: minimum score required before sending
- `notification.active_windows`: time windows where alerts are allowed
- `notification.quiet_hours`: time range where alerts stay quiet
- `notification.followup_tracking`: post-alert tracking behavior
- `notification.daily_summary`: scheduled daily market review and forecast

The example `config.json` is sanitized for open-source use:

- alerts are disabled by default
- the target handle is blank
- reply language defaults to English

Before using alerts or chat replies, set your own target:

```json
{
  "notification": {
    "enabled": true,
    "target": "your-imessage-handle",
    "reply_language": "en"
  }
}
```

Example daily summary configuration:

```json
{
  "notification": {
    "daily_summary": {
      "enabled": true,
      "send_times": ["09:00"],
      "attach_chart": true,
      "llm_enabled": true,
      "llm_timeout_seconds": 120,
      "openclaw_agent_id": "eth-daily-summary",
      "thinking": "off"
    }
  }
}
```

How it works:

- the watcher checks the configured times every polling cycle
- once the scheduled time has passed, it sends one summary for that day
- the LLM writes the market review and short-term forecast
- if the LLM call fails, the watcher falls back to a local rule-based summary

Recommended for lower token usage:

- use a dedicated OpenClaw agent for daily summaries instead of your main coding agent
- the repository example already uses `openclaw_agent_id: "eth-daily-summary"`
- if you do not want a separate agent, change it back to `main`

Example `~/.openclaw/openclaw.json` agent snippet:

```json
{
  "agents": {
    "list": [
      {
        "id": "main",
        "default": true,
        "workspace": "/path/to/your/main/workspace"
      },
      {
        "id": "eth-daily-summary",
        "name": "ETH Daily Summary",
        "workspace": "/path/to/a/small/separate/workspace",
        "model": {
          "primary": "yinli/claude-sonnet-4-6",
          "fallbacks": []
        },
        "thinkingDefault": "off"
      }
    ]
  }
}
```

Why this matters:

- using `main` may inject a large coding workspace context into each daily summary call
- a dedicated lightweight agent can reduce token usage significantly
- after editing `~/.openclaw/openclaw.json`, run `openclaw gateway restart`

## Strategy Profiles

- `scalp`: faster, more sensitive, more noise
- `balanced`: default profile, balanced between speed and filtering
- `swing`: slower and more conservative

Example:

```json
{
  "strategy_profile": "swing"
}
```

## Position Tracking

If you want follow-up messages to use your real filled price instead of the
signal reference price:

1. Run `position-open` after you enter.
2. Run `position-close` after you exit.

This makes floating PnL, distance to stop, and distance to take profit much
closer to your real trade.

## Runtime Copy

If you want to keep your working copy separate from the long-running background
instance, deploy a runtime copy:

```bash
cd /path/to/eth-invest-agent
./deploy_runtime_copy.sh
ETH_AGENT_HOME="$HOME/.clawdbot/apps/eth-invest-agent" "$HOME/.clawdbot/apps/eth-invest-agent/install_launch_agent.sh"
```

This is useful if you want:

- one directory for editing source code
- one stable directory for the background service

## Repository Layout

- `scripts/eth_watcher.py`: market fetch, indicators, signal scoring, alerting
- `config.json`: local configuration
- `state/runtime.json`: runtime state
- `hooks/eth-chat/`: OpenClaw hook for inbound message replies
- `deploy_runtime_copy.sh`: copy the project to a stable runtime location

## Notes

- The watcher includes cooldowns, daily alert caps, and repeat filters, so it
  should not spam you every minute.
- This project is a rule-based assistant, not a profit guarantee.
