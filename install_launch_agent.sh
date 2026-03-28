#!/bin/zsh
set -euo pipefail

LABEL="ai.openclaw.eth-watcher"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export ETH_AGENT_HOME="${ETH_AGENT_HOME:-$SCRIPT_DIR}"
PLIST_TEMPLATE="$SCRIPT_DIR/ai.openclaw.eth-watcher.plist"
RUN_SCRIPT="$SCRIPT_DIR/run_launch_agent.sh"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents"
/usr/bin/python3 - <<'PY' "$PLIST_TEMPLATE" "$PLIST_DST" "$RUN_SCRIPT" "$ETH_AGENT_HOME"
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text()
rendered = (
    template
    .replace("__RUN_SCRIPT__", sys.argv[3])
    .replace("__WORKING_DIR__", sys.argv[4])
)
Path(sys.argv[2]).write_text(rendered)
PY

launchctl bootout "$DOMAIN" "$PLIST_DST" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$PLIST_DST"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo "Installed and started $LABEL"
echo "Logs: /tmp/eth-invest-agent-daemon.log"
