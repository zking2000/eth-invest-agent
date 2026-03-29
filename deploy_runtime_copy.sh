#!/bin/zsh
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Runtime copy deployment has been retired."
echo "Live runtime source: $SOURCE_DIR"
echo "Use OpenClaw cron with this repository directly."
echo "No files were copied."
