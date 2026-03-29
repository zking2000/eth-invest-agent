#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export ETH_AGENT_HOME="${ETH_AGENT_HOME:-$SCRIPT_DIR}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
CONFIG_PATH="$ETH_AGENT_HOME/config.json"
if [ -f "$ETH_AGENT_HOME/config.local.json" ]; then
  CONFIG_PATH="$ETH_AGENT_HOME/config.local.json"
fi

exec /usr/bin/python3 "$ETH_AGENT_HOME/scripts/eth_watcher.py" \
  --config "$CONFIG_PATH" \
  --state "state/runtime.json" \
  daemon
