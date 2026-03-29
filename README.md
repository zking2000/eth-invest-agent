# ETH Watcher

`ETH Watcher` is now a modular ETH analysis framework built around `OpenClaw`.
It keeps the original alert/chat workflow, but now also includes data download,
feature engineering, XGBoost training, ML-assisted live scoring, Backtrader
backtests, and chart generation.

[English](./README.md) | [简体中文](./README.zh-CN.md)

When updating project documentation, keep `README.md` and
`README.zh-CN.md` in sync.

## What It Includes

- Rule-based ETH setup detection on `5m`, `15m`, `1h`, and `4h`
- Technical indicators with `Pandas` and `NumPy`
- Historical market data download with `CCXT`
- Feature engineering pipeline for supervised learning
- `XGBoost` signal model training and model artifact persistence
- `Backtrader` backtests that reuse the same feature/model flow
- SVG and Matplotlib chart support
- Existing `OpenClaw` iMessage alerts, chat replies, daily summaries, and cron mode

## Install

```bash
cd /path/to/eth-invest-agent
python3 -m pip install -r requirements.txt
```

For open-source use, keep `config.sample.json` as the public template and put
your real personal settings into `config.local.json` (ignored by git). When
`config.local.json` exists, the watcher will use it automatically.

If you want outbound alerts or chat replies, start from `config.sample.json`
and create your local config:

```bash
cp config.sample.json config.local.json
```

Then update `config.local.json`:

```json
{
  "display": {
    "price_currency": "CNY",
    "usd_cny_rate": 7.2,
    "use_live_fx": true,
    "live_fx_cache_minutes": 60
  },
  "notification": {
    "enabled": true,
    "target": "your-imessage-handle",
    "reply_language": "en"
  }
}
```

The repository example is sanitized for open-source use, so alerts are disabled
and the target is blank by default.

`notification.reply_language` supports:

- `zh` for Simplified Chinese messages
- `en` for English messages

`display.price_currency` currently supports:

- `CNY` to show message prices in RMB
- `USD` to show message prices in USD

When `CNY` is enabled, watcher alerts, chat replies, follow-up messages, daily
summaries, and snapshot output all display price levels in RMB. By default, the
watcher now fetches a live USD/CNY rate and caches it locally; `usd_cny_rate`
acts as a fallback if the FX endpoint is temporarily unavailable.

## Quick Start

```bash
python3 ./scripts/eth_watcher.py snapshot
python3 ./scripts/eth_watcher.py run-once --send --dry-run
python3 ./scripts/eth_watcher.py chat-query --message "Can I buy now?"
python3 ./scripts/eth_watcher.py download-history --limit 300 --output data/binance_ethusdt_15m.csv
python3 ./scripts/eth_watcher.py build-features --input data/binance_ethusdt_15m.csv --output data/features_ethusdt_15m.csv
python3 ./scripts/eth_watcher.py train-model --features data/features_ethusdt_15m.csv
python3 ./scripts/eth_watcher.py backtest --input data/binance_ethusdt_15m.csv
python3 ./scripts/eth_watcher.py sweep-backtest --input data/binance_ethusdt_15m.csv
```

## Safe Push And Live Runtime

The current repository directory is now the default live runtime source.
`OpenClaw cron`, the chat hook, `config.local.json`, and `state/runtime.json`
all run directly from this repo instead of a separate runtime copy under
`~/.clawdbot/apps/eth-invest-agent`.

For day-to-day publishing, use the built-in safe flow instead of running
`git push` manually:

```bash
chmod +x ./scripts/push_and_sync.sh
./scripts/push_and_sync.sh
```

This flow now does two things in order:

1. Audits **tracked files only** for likely private data or secrets.
2. Pushes `HEAD` to `origin`.

To run only the tracked-file privacy audit:

```bash
python3 ./scripts/audit_tracked_files.py
```

Because this repository itself is the live runtime source, no extra sync step is
needed after a successful push.

## ML Workflow

### 1. Download historical candles

```bash
python3 ./scripts/eth_watcher.py download-history \
  --exchange binance \
  --symbol ETH/USDT \
  --timeframe 15m \
  --limit 2000 \
  --output data/binance_ethusdt_15m.csv
```

### 2. Build features

```bash
python3 ./scripts/eth_watcher.py build-features \
  --input data/binance_ethusdt_15m.csv \
  --output data/features_ethusdt_15m.csv \
  --horizon 4 \
  --threshold-pct 0.35
```

The feature pipeline currently includes:

- returns and rolling volatility
- volume changes and z-scores
- `EMA20`, `EMA50`, `RSI14`, `ATR14`, `MACD histogram`
- breakout and pullback-related derived fields
- future-return labels for `buy / hold / sell` style mapping

### 3. Train the XGBoost model

```bash
python3 ./scripts/eth_watcher.py train-model \
  --features data/features_ethusdt_15m.csv \
  --model-output models/xgboost_eth_signal.json \
  --metadata-output models/xgboost_eth_signal.meta.json
```

This writes:

- model weights to `models/`
- feature metadata to `models/`
- a latest prediction preview to stdout

### 4. Run a backtest

```bash
python3 ./scripts/eth_watcher.py backtest \
  --input data/binance_ethusdt_15m.csv \
  --model-path models/xgboost_eth_signal.json \
  --metadata-path models/xgboost_eth_signal.meta.json \
  --entry-prob-threshold 0.34 \
  --exit-prob-threshold 0.42 \
  --min-hold-bars 2
```

By default this now creates a timestamped report directory under `reports/backtest/`
containing:

- `summary.json`
- `trades.csv`
- `equity_curve.csv`
- `price_signals.png`
- `equity_curve.png`
- `monthly_returns.png`

The watcher also maintains `reports/latest/backtest` as a symlink to the newest
backtest report, so you can inspect the latest run without searching by date.

The current backtest layer uses `Backtrader` and a simple risk-based entry model:

- position sizing from risk fraction
- ATR-based stop loss
- reward/risk take profit
- exit on stop, target, bearish signal flip, or bearish probability flip
- configurable probability thresholds to relax or tighten trade frequency

Current backtest output includes:

- total return
- win rate
- max drawdown
- annualized volatility
- annualized Sharpe ratio
- monthly returns
- exit reason distribution
- best and worst trade
- full trade ledger with entry/exit fields

### 4a. Sweep parameter combinations

```bash
python3 ./scripts/eth_watcher.py sweep-backtest \
  --input data/binance_ethusdt_15m.csv \
  --entry-prob-thresholds 0.28,0.30,0.34 \
  --exit-prob-thresholds 0.34,0.36,0.42 \
  --stop-loss-atrs 1.0,1.3,1.6 \
  --top 10
```

This writes a timestamped directory under `reports/sweeps/` with:

- `summary.json`
- `grid.csv`
- one return heatmap per `stop_loss_atr`

It also maintains `reports/latest/sweeps` as a symlink to the newest sweep
report.

By default, `sweep-backtest` now automatically picks the top-ranked parameter
set and writes it back to `config.json` under:

- `ml.backtest_defaults`
- `ml.recommended_backtest`

That means later `backtest` runs can reuse the recommended defaults even when
you do not pass thresholds manually. Use `--no-apply-best-to-config` if you
only want to inspect sweep results without updating config.

### 5. Use the model in live watcher flow

Once the default model files exist in `models/`, `snapshot`, `run-once`, and
optional `daemon` mode automatically try to enrich the rule-based analysis with model
probabilities.

Current live fusion behavior:

- rules remain the primary signal engine
- the ML model adds an assist score and probability summary
- strong bullish model confirmation can upgrade a `near_buy`
- bearish model disagreement can downgrade an aggressive signal
- alert, chat, and daily summary output now also include a direct action bias:
  buy, probe buy, wait, reduce, hold, or sell
- user-facing price output can now be switched between `CNY` and `USD`
- message language can now be switched between Chinese and English

## Alerts And Chat

### Local CLI usage

```bash
python3 ./scripts/eth_watcher.py snapshot
python3 ./scripts/eth_watcher.py chat-query --message "How far is price from the entry?"
python3 ./scripts/eth_watcher.py position-status
```

### Default cron mode

This is now the recommended production mode. The watcher is scheduled through
`OpenClaw cron` and runs the current repository directly:

```bash
openclaw cron list
openclaw cron runs --id 52bfec18-3cac-42b4-95a3-77547800b40b --limit 5
```

Current default behavior:

- the repo directory is the only live code source
- `config.local.json` is the private runtime config
- `state/runtime.json` stores runtime state and daily summary audits
- `eth-watcher-minute` runs `run-once --send` every minute through OpenClaw

### Optional daemon mode

```bash
python3 ./scripts/eth_watcher.py daemon
```

The watcher can still send:

- trade alerts
- follow-up tracking messages
- one or more scheduled daily summaries

### iMessage chat mode

Enable the hook:

```bash
openclaw hooks enable eth-chat
openclaw gateway restart
```

Then ask questions like:

- `ETH`
- `Can I buy now?`
- `Should I sell now?`
- `How far is price from the entry?`
- `Can I start with a small size?`
- `How is it performing now?`
- `Position status`
- `Why this view?`
- `HELP`

Chinese prompts are also supported.

### Verify the hook

```bash
openclaw hooks list --verbose
rg "Registered hook: eth-chat|eth-chat" ~/.openclaw/logs/gateway.log
```

## Daily Summary And Cron

Example daily summary config:

```json
{
  "display": {
    "price_currency": "CNY",
    "usd_cny_rate": 7.2,
    "use_live_fx": true,
    "live_fx_cache_minutes": 60
  },
  "notification": {
    "reply_language": "zh",
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

With `notification.enabled=true`, a valid iMessage `target`, and the OpenClaw
hook/default cron setup enabled, the daily summary flow is:

1. `run-once --send` fetches fresh ETH data, usually from the default OpenClaw cron job.
2. The watcher generates rule + ML analysis with a buy/sell/wait recommendation.
3. `send_daily_summary()` sends the brief through OpenClaw.
4. OpenClaw delivers it to your configured iMessage target.

For day-to-day usage, this gives you:

- real-time ETH setup detection
- buy / probe-buy / wait / reduce / sell style recommendations
- scheduled daily market review and 24h outlook through iMessage

For lower token cost:

- use a dedicated `eth-daily-summary` agent
- keep its workspace small
- prefer a local model if possible

The watcher stores recent summary delivery audits in `state/runtime.json` under:

- `daily_summary.last_audit`
- `daily_summary.audit_history`

`OpenClaw cron` is now the default recommended scheduler. Keep `daemon` only as
an optional manual fallback for local experiments.

## Important Config Fields

- `symbol`: default `ETHUSDT`
- `strategy_profile`: `scalp`, `balanced`, or `swing`
- `display.price_currency`: `CNY` or `USD` for user-facing price output
- `display.usd_cny_rate`: fallback FX conversion used when live RMB pricing is unavailable
- `display.use_live_fx`: whether RMB display should fetch a live USD/CNY rate
- `display.live_fx_cache_minutes`: how long the live FX rate is cached locally
- `notification.*`: alert, follow-up, chat, and daily summary behavior
- `notification.reply_language`: `zh` or `en` for all outbound messages
- `ml.enabled`: whether live watcher tries model-assisted scoring
- `ml.model_path`: default saved model path
- `ml.metadata_path`: feature metadata path
- `ml.feature_limit`: number of recent bars used for live feature building
- `ml.target_horizon`: future label horizon
- `ml.target_threshold_pct`: label threshold in percent

## Repository Layout

- `scripts/eth_watcher.py`: orchestration entrypoint and CLI
- `eth_agent/config.py`: config defaults and loading
- `eth_agent/state.py`: runtime state defaults and normalization
- `eth_agent/data/`: Binance fetchers and `CCXT` downloader
- `eth_agent/features/`: indicators and feature engineering
- `eth_agent/models/`: XGBoost training and inference
- `eth_agent/strategy/`: rule-based signal engine
- `eth_agent/risk/`: tracking and risk helpers
- `eth_agent/backtest/`: Backtrader integration
- `eth_agent/visualization/`: SVG and Matplotlib charts
- `hooks/eth-chat/`: OpenClaw chat hook
- `scripts/push_and_sync.sh`: tracked-file privacy audit + `git push`
- `config.sample.json`: public-safe example config
- `config.local.json`: private local override config (gitignored)
- `config.json`: fallback local config
- `state/runtime.json`: runtime state
- `deploy_runtime_copy.sh`: legacy compatibility stub, no longer used for live runtime

## Notes

- The live watcher still keeps the original rule engine as the primary decision maker.
- The ML layer is an assistive confirmation layer, not a fully autonomous trading bot.
- This repository is for analysis and experimentation, not financial advice.
