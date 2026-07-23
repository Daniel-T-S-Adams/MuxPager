#!/usr/bin/env python3
"""Fail when tracked project files resemble real Telegram credentials."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(rb"(?<![0-9])[0-9]{8,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")
ASSIGNMENT_RE = re.compile(rb"^TMUX_TELEGRAM_BOT_TOKEN=\s*\S+", re.MULTILINE)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        PROJECT_ROOT / raw.decode("utf-8")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content:
            continue
        relative = path.relative_to(PROJECT_ROOT)
        if TOKEN_RE.search(content):
            failures.append(f"{relative}: resembles a Telegram bot token")
        if (
            relative.name in {"env", ".env"}
            and ASSIGNMENT_RE.search(content)
        ):
            failures.append(f"{relative}: contains a non-empty bot token assignment")
    history = subprocess.run(
        ["git", "log", "--all", "-p", "--format="],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    if TOKEN_RE.search(history):
        failures.append("Git history resembles a Telegram bot token")
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print("tracked secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
