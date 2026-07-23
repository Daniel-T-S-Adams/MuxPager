#!/usr/bin/env bash

# Persist the exact final assistant response supplied by Codex and Claude Stop
# hooks.  Consumers such as the Telegram bridge can then display the response
# without scraping terminal UI chrome from capture-pane.

cache_last_assistant_message() {
    local hook_json="$1"
    local tmux_session="$2"
    local tmux_pane="${3:-}"

    [ -n "$hook_json" ] || return 0
    [ -n "$tmux_session" ] || return 0
    [ -n "$tmux_pane" ] || return 0
    command -v python3 >/dev/null 2>&1 || return 0

    local target="$PANE_DIR/${tmux_session}_${tmux_pane}.last-message"
    printf '%s' "$hook_json" | python3 -c '
import json
import os
import sys

target = sys.argv[1]
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

message = payload.get("last_assistant_message")
if not isinstance(message, str) or not message.strip():
    raise SystemExit(0)

temporary = f"{target}.tmp.{os.getpid()}"
try:
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")
    os.replace(temporary, target)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
' "$target" >/dev/null 2>&1 || true
}
