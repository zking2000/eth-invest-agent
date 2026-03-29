#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[1/2] Auditing tracked files for private data..."
/usr/bin/python3 "$ROOT_DIR/scripts/audit_tracked_files.py"

echo "[2/2] Pushing current HEAD to origin..."
git -C "$ROOT_DIR" push origin HEAD

echo "Push completed."
echo "Current repository directory remains the live runtime source."
