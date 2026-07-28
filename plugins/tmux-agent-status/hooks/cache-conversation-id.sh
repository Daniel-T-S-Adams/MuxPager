#!/usr/bin/env bash

# Persist the stable Codex/Claude conversation identifier associated with a
# tmux pane. The hook payload is parsed with Python so JSON escaping cannot
# corrupt the deliberately simple two-line cache format:
#
#   <agent>
#   <session_id>

# Writes are atomic and private. SessionEnd only removes an identity when its
# payload still matches the cached value, protecting a new conversation that
# may already have reused the pane.

cache_conversation_identity() {
    local hook_json="$1"
    local tmux_session="$2"
    local tmux_pane="${3:-}"
    local agent="$4"

    [ -n "$hook_json" ] || return 0
    [ -n "$tmux_session" ] || return 0
    [ -n "$tmux_pane" ] || return 0
    command -v python3 >/dev/null 2>&1 || return 0

    local identity_file="$PANE_DIR/${tmux_session}_${tmux_pane}.conversation"
    printf '%s' "$hook_json" | python3 -c '
import json
import os
from pathlib import Path
import sys

target = Path(sys.argv[1])
agent = sys.argv[2]
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

session_id = payload.get("session_id")
if (
    agent not in {"codex", "claude"}
    or not isinstance(session_id, str)
    or not session_id
    or len(session_id) > 256
    or any(ord(character) < 32 or ord(character) == 127 for character in session_id)
):
    raise SystemExit(0)

target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
try:
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(f"{agent}\n{session_id}\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
' "$identity_file" "$agent" >/dev/null 2>&1 || true
}

clear_conversation_identity() {
    local hook_json="$1"
    local tmux_session="$2"
    local tmux_pane="${3:-}"
    local agent="$4"

    [ -n "$hook_json" ] || return 0
    [ -n "$tmux_session" ] || return 0
    [ -n "$tmux_pane" ] || return 0
    command -v python3 >/dev/null 2>&1 || return 0

    local identity_file="$PANE_DIR/${tmux_session}_${tmux_pane}.conversation"
    printf '%s' "$hook_json" | python3 -c '
import json
from pathlib import Path
import sys

target = Path(sys.argv[1])
agent = sys.argv[2]
try:
    payload = json.load(sys.stdin)
    cached_agent, cached_session_id = target.read_text(encoding="utf-8").splitlines()[:2]
except (json.JSONDecodeError, OSError, ValueError):
    raise SystemExit(0)

session_id = payload.get("session_id")
if session_id == cached_session_id and agent == cached_agent:
    target.unlink(missing_ok=True)
' "$identity_file" "$agent" >/dev/null 2>&1 || true
}
