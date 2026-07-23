#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
TEST_SOCKET="tmux-agent-portability-$$"

cleanup() {
    tmux -L "$TEST_SOCKET" kill-server 2>/dev/null || true
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

RELOCATED_ROOT="$TEST_ROOT/project with spaces"
TEST_HOME="$TEST_ROOT/home"
mkdir -p "$RELOCATED_ROOT" "$TEST_HOME"
cp -a \
    "$PROJECT_ROOT/.tmux.conf" \
    "$PROJECT_ROOT/plugins" \
    "$PROJECT_ROOT/scripts" \
    "$RELOCATED_ROOT/"

HOME="$TEST_HOME" "$RELOCATED_ROOT/scripts/setup-runtime-links.sh"

HOME="$TEST_HOME" tmux -L "$TEST_SOCKET" \
    -f "$RELOCATED_ROOT/.tmux.conf" new-session -d -s portability

detected_root="$(
    tmux -L "$TEST_SOCKET" show-environment -g TMUX_AGENT_PROJECT_ROOT |
        sed 's/^[^=]*=//'
)"
if [ "$detected_root" != "$RELOCATED_ROOT" ]; then
    echo "expected relocated root '$RELOCATED_ROOT', got '$detected_root'" >&2
    exit 1
fi

python3 - "$PROJECT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for name in ("codex-hooks.json", "claude-hooks.json"):
    payload = json.loads((root / "config" / name).read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for matchers in payload["hooks"].values()
        for matcher in matchers
        for hook in matcher["hooks"]
    ]
    assert commands
    assert all("$HOME/.local/libexec/tmux-agent-console/hooks/" in command for command in commands)
    assert all("/my-tmux/" not in command for command in commands)

unit = (root / "systemd" / "tmux-telegram-bridge.service").read_text(
    encoding="utf-8"
)
assert "%h/my-tmux" not in unit
assert "ExecStart=%h/.local/bin/tmux-telegram-bridge" in unit
PY

if [ "$(readlink -f "$TEST_HOME/.local/bin/tmux-telegram-bridge")" != \
    "$RELOCATED_ROOT/scripts/tmux-telegram-bridge.py" ]; then
    echo "Telegram bridge runtime link points at the wrong checkout" >&2
    exit 1
fi
if [ "$(readlink -f "$TEST_HOME/.local/libexec/tmux-agent-console/hooks")" != \
    "$RELOCATED_ROOT/plugins/tmux-agent-status/hooks" ]; then
    echo "agent hook runtime link points at the wrong checkout" >&2
    exit 1
fi

echo "installation path portability checks passed"
