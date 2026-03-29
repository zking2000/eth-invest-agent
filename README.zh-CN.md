# ETH Watcher

`ETH Watcher` 是一个基于 `OpenClaw` 的本地 ETH 监控脚本。

[English](./README.md) | [简体中文](./README.zh-CN.md)

更新项目文档时，请同步维护 `README.md` 和 `README.zh-CN.md` 两个文件。

这个项目的目标是：

- 以较低的持续成本监控 `ETHUSDT`
- 在本地用规则打分判断买点
- 通过 `iMessage` 发送提醒
- 支持简单的入站聊天问答
- 支持更灵敏或更保守的交易风格切换

## 它能做什么

脚本会拉取 Binance 公共市场数据，计算技术指标，并评估三类交易形态：

- `pullback`：趋势回踩到均线支撑附近
- `breakout`：放量突破近期 `15m` 高点
- `reversal`：超跌后的快速反弹修复

使用的周期：

- `5m`
- `15m`
- `1h`
- `4h`

使用的指标：

- `EMA20 / EMA50`
- `RSI14`
- `ATR14`
- `MACD histogram`

## 你可以如何与它互动

这个监控器主要有两种互动方式：本地命令行和 `iMessage` 聊天。

### 开始前先配置

仓库里的示例配置是为了适合开源发布而特意做成安全默认值：

- 默认关闭提醒
- `iMessage` 目标为空
- 默认使用英文回复

如果你希望收到提醒或聊天回复，需要先在 `config.json` 中填入自己的目标：

```json
{
  "notification": {
    "enabled": true,
    "target": "your-imessage-handle",
    "reply_language": "en"
  }
}
```

如果 `notification.target` 为空，脚本就没有可以发送提醒或回复的目标。

### 1. 在终端本地交互

你可以不经过 `iMessage`，直接在终端中查询当前判断：

```bash
python3 ./scripts/eth_watcher.py snapshot
python3 ./scripts/eth_watcher.py chat-query --message "现在能买吗？"
python3 ./scripts/eth_watcher.py chat-query --message "距离买点多远？"
python3 ./scripts/eth_watcher.py position-status
```

这些命令的作用分别是：

- `snapshot`：打印当前市场判断和各信号评分
- `chat-query --message "..."`
：调用和聊天回复相同的问答逻辑
- `position-status`：查看当前记录的持仓状态

### 2. 被动接收提醒

你也可以让脚本一直运行，在规则命中时主动推送提醒：

```bash
python3 ./scripts/eth_watcher.py daemon
```

提醒消息通常会包含：

- 形态类型
- 入场区间
- 止损位
- 止盈位
- 盈亏比
- 仓位建议
- 强度评分

即使当天没有触发买点，daemon 也可以按固定时间发送一条“每日市场评价与预测”。仓库里的默认示例配置是每天 `09:00` 发送一条。

### 3. 通过 iMessage 主动提问

项目里包含一个 OpenClaw hook，你可以直接给它发消息，让它回复，而不是只等它推送提醒。

先启用 hook：

```bash
openclaw hooks enable eth-chat
openclaw gateway restart
```

然后向 `config.json` 中配置的同一个 `iMessage` 目标发送消息。

例如：

- `ETH`
- `现在能买吗？`
- `距离买点多远？`
- `可以先小仓试一下吗？`
- `当前表现如何？`
- `持仓状态`
- `为什么这么判断？`
- `HELP`

英文提问也同样支持。

它通常可以回答这些类型的问题：

- 当前是 `watch`、`near buy` 还是 `buy trigger`
- 当前价格距离建议入场位还有多远
- 现在是否适合先少量买入
- 当前相对参考位或持仓价的表现
- 当前记录的持仓状态
- 当前判断背后的主要理由

### 3a. 如何确认 Hook 已成功注册

先检查 OpenClaw 是否已经识别到这个 hook：

```bash
openclaw hooks list --verbose
```

你应该能看到类似：

```text
eth-chat   ✓ ready
```

再检查 gateway 是否已经在运行时注册它：

```bash
rg "Registered hook: eth-chat|eth-chat" ~/.openclaw/logs/gateway.log
```

你应该能看到类似：

```text
Registered hook: eth-chat -> message:preprocessed
```

如果还没有出现，可以先重启 gateway，再重新检查：

```bash
openclaw gateway restart
openclaw hooks list --verbose
```

### 4. 记录真实持仓以获得更准确的跟踪

如果你希望脚本后续提到的是你的真实成交价，而不是它自己的参考入场价：

```bash
python3 ./scripts/eth_watcher.py position-open --entry-price 1988.5 --size "10%"
python3 ./scripts/eth_watcher.py position-status
python3 ./scripts/eth_watcher.py position-close
```

记录持仓后，后续的跟踪消息会更贴近你的真实仓位表现。

## 回复语言

你可以选择提醒消息和聊天回复使用英文还是中文。

在 `config.json` 中设置：

```json
{
  "notification": {
    "reply_language": "en"
  }
}
```

支持的值：

- `"en"`：英文回复
- `"zh"`：中文回复

这个配置会影响：

- 提醒消息
- 跟踪消息
- 聊天问答回复
- 脚本在终端输出的文本快照

## 快速开始

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

如果你不想长期挂一个独立 daemon，也可以改用 `OpenClaw cron` 每分钟执行一次 watcher。

你也可以显式指定项目根目录：

```bash
export ETH_AGENT_HOME="/path/to/eth-invest-agent"
python3 "$ETH_AGENT_HOME/scripts/eth_watcher.py" snapshot
```

## 配置项说明

`config.json` 中比较重要的字段包括：

- `symbol`：交易对，默认 `ETHUSDT`
- `strategy_profile`：`scalp`、`balanced` 或 `swing`
- `notification.enabled`：是否启用主动提醒
- `notification.target`：你的 `iMessage` 目标
- `notification.reply_language`：`en` 或 `zh`
- `notification.cooldown_minutes`：提醒之间的最短冷却时间
- `notification.max_alerts_per_day`：单日提醒上限
- `notification.min_score_to_alert`：发送提醒所需的最低分数
- `notification.active_windows`：允许提醒的时间窗口
- `notification.quiet_hours`：静默时间段
- `notification.followup_tracking`：提醒后跟踪逻辑
- `notification.daily_summary`：每日固定时段市场评价与预测

仓库内提供的 `config.json` 是为开源使用准备的：

- 默认关闭提醒
- 目标句柄为空
- 默认使用英文回复

真正使用前，请填上你自己的配置：

```json
{
  "notification": {
    "enabled": true,
    "target": "your-imessage-handle",
    "reply_language": "en"
  }
}
```

每日市场评价配置示例：

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

它的工作方式是：

- daemon 每轮都会检查是否到了设定发送时间
- 只要当天设定时间已过且当天还没发过，就会补发一条
- LLM 负责生成市场评价和短线预测
- 如果 LLM 调用失败，会自动降级成本地规则总结

如果你想明显降低 token 消耗，推荐这样配置：

- 给每日市场评价单独使用一个 `OpenClaw agent`，不要直接复用主 coding agent
- 仓库默认示例已经使用 `openclaw_agent_id: "eth-daily-summary"`
- 如果你不想单独建 agent，也可以把它改回 `main`

`~/.openclaw/openclaw.json` 的 agent 配置示例：

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
          "primary": "ollama/qwen2.5-coder:7b",
          "fallbacks": []
        },
        "thinkingDefault": "off"
      }
    ]
  }
}
```

这样做的原因：

- 如果直接走 `main`，每日摘要请求可能会带上较大的主工作区上下文
- 单独的轻量 agent 可以明显降低单次日报的 token 消耗
- 把 `eth-daily-summary` 切到本地模型，还可以进一步降低 API 成本
- 改完 `~/.openclaw/openclaw.json` 后，记得执行 `openclaw gateway restart`

每日摘要审计日志：

- watcher 会把最近一次每日日报发送审计写入 `state/runtime.json`
- 查看 `daily_summary.last_audit` 可以看到最近一次发送尝试
- 查看 `daily_summary.audit_history` 可以看到最近若干次发送记录
- 每条审计会记录发送时间、目标、语言、成功或失败状态、LLM 使用情况，以及可用时的消息 id

## 策略档位

- `scalp`：更快、更灵敏，但噪音更多
- `balanced`：默认档，速度和过滤能力更均衡
- `swing`：更慢、更保守

示例：

```json
{
  "strategy_profile": "swing"
}
```

## 持仓跟踪

如果你希望跟踪消息使用你的真实成交价，而不是脚本自己的参考入场价：

1. 开仓后执行 `position-open`
2. 平仓后执行 `position-close`

这样浮盈亏、距离止损、距离止盈等数据会更贴近你的真实交易。

## 运行副本

如果你希望把编辑代码的目录和长期后台运行的目录分开，可以部署一个运行副本：

```bash
cd /path/to/eth-invest-agent
./deploy_runtime_copy.sh
ETH_AGENT_HOME="$HOME/.clawdbot/apps/eth-invest-agent" "$HOME/.clawdbot/apps/eth-invest-agent/install_launch_agent.sh"
```

适合这些场景：

- 一个目录专门用于编辑源码
- 另一个稳定目录专门用于后台运行

## OpenClaw Cron 模式

你也可以不用长期运行 daemon，而改成用 `OpenClaw cron` 每分钟执行一次 watcher。

推荐方式：

- `eth-chat` 聊天问答继续保留
- 关闭原来的 `launchd daemon`
- 新建一个每分钟执行 `run-once --send` 的 cron job
- 给 cron 单独使用一个轻量 agent

`~/.openclaw/openclaw.json` 中的 cron agent 示例：

```json
{
  "agents": {
    "list": [
      {
        "id": "eth-watcher-cron",
        "name": "ETH Watcher Cron",
        "workspace": "/path/to/a/separate/cron-workspace",
        "model": {
          "primary": "ollama/qwen2.5-coder:7b",
          "fallbacks": []
        },
        "thinkingDefault": "off"
      }
    ]
  }
}
```

cron job 示例：

```bash
openclaw cron add \
  --name "eth-watcher-minute" \
  --every 1m \
  --session isolated \
  --agent eth-watcher-cron \
  --light-context \
  --no-deliver \
  --message "Use the exec tool exactly once. Run this command on the gateway host and do nothing else: /bin/zsh -lc 'export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin; /usr/bin/python3 \"/Users/you/.clawdbot/apps/eth-invest-agent/scripts/eth_watcher.py\" --config \"/Users/you/.clawdbot/apps/eth-invest-agent/config.json\" --state \"/Users/you/.clawdbot/apps/eth-invest-agent/state/runtime.json\" run-once --send'. After the command finishes, emit one short plain-text status line with the exit code and the key stdout or stderr result."
```

常用命令：

- `openclaw cron list`
- `openclaw cron runs --id <job-id>`
- `openclaw cron run <job-id>`

如果切到 cron 模式，不要让旧 daemon 同时继续运行，否则 watcher 可能会被重复执行。

## 仓库结构

- `scripts/eth_watcher.py`：行情获取、指标计算、信号评分、提醒发送
- `config.json`：本地配置
- `state/runtime.json`：运行时状态
- `hooks/eth-chat/`：OpenClaw 的入站聊天回复 hook
- `deploy_runtime_copy.sh`：复制项目到稳定运行目录

## 注意事项

- 脚本包含冷却时间、单日提醒上限和重复过滤逻辑，因此不会每分钟都刷屏。
- 这是一个基于规则的辅助工具，不代表收益保证。
