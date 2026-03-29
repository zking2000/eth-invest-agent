#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

ALLOWLIST_SUBSTRINGS = {
    "your-imessage-handle",
    "/path/to/eth-invest-agent",
}

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI/Gemini style key", re.compile(r"\b(?:sk-|AIza)[A-Za-z0-9\-_]{16,}\b")),
    ("Slack token", re.compile(r"\b(?:xox[baprs]-|xapp-)[A-Za-z0-9-]+\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")),
    ("Chinese mainland phone", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")),
    ("Email address", re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("Absolute macOS home path", re.compile(r"/Users/[^/\s]+")),
]


def git_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line.strip()]


def is_text_file(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:2048]
    except Exception:
        return False
    return b"\x00" not in sample


def scan_file(path: Path) -> list[str]:
    if not is_text_file(path):
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="ignore")
    findings: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        if any(token in line for token in ALLOWLIST_SUBSTRINGS):
            continue
        for label, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            snippet = line.strip()
            if len(snippet) > 180:
                snippet = snippet[:177] + "..."
            findings.append(f"{path.relative_to(ROOT)}:{line_no}: {label}: {snippet}")
    return findings


def main() -> int:
    findings: list[str] = []
    for path in git_tracked_files():
        findings.extend(scan_file(path))

    if findings:
        print("Tracked-file privacy audit failed. Review these findings:", file=sys.stderr)
        for item in findings:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Tracked-file privacy audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
