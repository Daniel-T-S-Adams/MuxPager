#!/usr/bin/env bash

# Queue exact prompts submitted directly in Codex or Claude so the Telegram
# bridge can mirror the local side of the conversation. Prompts injected by the
# bridge carry a short-lived marker and are skipped because their original
# Telegram message is already present in the topic.

cache_local_user_prompt() {
    local hook_json="$1"
    local tmux_session="$2"
    local tmux_pane="${3:-}"

    [ -n "$hook_json" ] || return 0
    [ -n "$tmux_session" ] || return 0
    [ -n "$tmux_pane" ] || return 0
    command -v python3 >/dev/null 2>&1 || return 0

    local prompt_dir="$PANE_DIR/${tmux_session}_${tmux_pane}.prompts"
    local telegram_marker="$PANE_DIR/${tmux_session}_${tmux_pane}.telegram-prompt"
    printf '%s' "$hook_json" | python3 -c '
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

prompt_dir = Path(sys.argv[1])
telegram_marker = Path(sys.argv[2])
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

prompt = payload.get("prompt")
if not isinstance(prompt, str) or not prompt.strip():
    raise SystemExit(0)

# The bridge writes this marker immediately before pasting Telegram text into
# the pane. Consume an exact, recent match so that message is not mirrored back
# into the topic as a duplicate. A mismatch is retained briefly because local
# input and Telegram delivery can race without referring to the same prompt.
try:
    marker = json.loads(telegram_marker.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    marker = None
if isinstance(marker, dict):
    created_at = marker.get("created_at")
    is_recent = (
        isinstance(created_at, (int, float))
        and time.time() - float(created_at) <= 60
    )
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if is_recent and marker.get("prompt_sha256") == prompt_sha256:
        telegram_marker.unlink(missing_ok=True)
        raise SystemExit(0)
    if not is_recent:
        telegram_marker.unlink(missing_ok=True)

prompt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
name = f"{time.time_ns():020d}-{os.getpid()}-{secrets.token_hex(4)}.prompt"
target = prompt_dir / name
temporary = prompt_dir / f".{name}.tmp"
try:
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(prompt)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
' "$prompt_dir" "$telegram_marker" >/dev/null 2>&1 || true
}
