#!/usr/bin/env bash

# Persist the exact final assistant response supplied by Codex and Claude Stop
# hooks.  Consumers such as the Telegram bridge can then display the response
# without scraping terminal UI chrome from capture-pane.

cache_last_assistant_message() {
    local hook_json="$1"
    local tmux_session="$2"
    local tmux_pane="${3:-}"
    local agent="${4:-}"

    [ -n "$hook_json" ] || return 0
    [ -n "$tmux_session" ] || return 0
    [ -n "$tmux_pane" ] || return 0
    command -v python3 >/dev/null 2>&1 || return 0

    local legacy_target="$PANE_DIR/${tmux_session}_${tmux_pane}.last-message"
    printf '%s' "$hook_json" | python3 -c '
import hashlib
import json
import os
from pathlib import Path
import sys

pane_dir = Path(sys.argv[1])
legacy_target = Path(sys.argv[2])
agent = sys.argv[3]
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

message = payload.get("last_assistant_message")
if not isinstance(message, str) or not message.strip():
    raise SystemExit(0)

session_id = payload.get("session_id")
if isinstance(session_id, str) and session_id and agent in {"codex", "claude"}:
    cache_id = hashlib.sha256(
        f"{agent}\0{session_id}".encode("utf-8")
    ).hexdigest()
    target = pane_dir / "conversations" / f"{cache_id}.last-message"
else:
    target = legacy_target
target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

temporary = Path(f"{target}.tmp.{os.getpid()}")
try:
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
' "$PANE_DIR" "$legacy_target" "$agent" >/dev/null 2>&1 || true
}
