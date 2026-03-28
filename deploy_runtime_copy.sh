#!/bin/zsh
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="${ETH_AGENT_RUNTIME_HOME:-$HOME/.clawdbot/apps/eth-invest-agent}"

mkdir -p "$RUNTIME_DIR"

/usr/bin/python3 - <<'PY' "$SOURCE_DIR" "$RUNTIME_DIR"
from pathlib import Path
import shutil
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

exclude_names = {
    ".DS_Store",
    "__pycache__",
}

for item in src.iterdir():
    if item.name in exclude_names:
        continue
    target = dst / item.name
    if item.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    else:
        shutil.copy2(item, target)
PY

chmod +x "$RUNTIME_DIR/install_launch_agent.sh" "$RUNTIME_DIR/run_launch_agent.sh"

echo "Runtime copy deployed to: $RUNTIME_DIR"
echo "To install background service from runtime copy:"
echo "  ETH_AGENT_HOME=\"$RUNTIME_DIR\" \"$RUNTIME_DIR/install_launch_agent.sh\""
