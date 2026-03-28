#!/bin/zsh
set -euo pipefail

LABEL="ai.openclaw.eth-watcher"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN" "$PLIST_DST" >/dev/null 2>&1 || true
rm -f "$PLIST_DST"

echo "Removed $LABEL"
