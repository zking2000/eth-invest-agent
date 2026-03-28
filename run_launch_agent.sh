#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export ETH_AGENT_HOME="${ETH_AGENT_HOME:-$SCRIPT_DIR}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

exec /usr/bin/python3 "$ETH_AGENT_HOME/scripts/eth_watcher.py" \
  --config "config.json" \
  --state "state/runtime.json" \
  daemon
