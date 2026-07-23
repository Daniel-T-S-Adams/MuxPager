#!/usr/bin/env bash

set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
FAILURES=0

pass() {
    printf '✓ %s\n' "$1"
}

warn() {
    printf '! %s\n' "$1"
}

fail() {
    printf '✗ %s\n' "$1"
    FAILURES=$((FAILURES + 1))
}

for command_name in tmux python3 fzf ps pgrep systemctl; do
    if command -v "$command_name" >/dev/null 2>&1; then
        pass "$command_name is installed"
    else
        fail "$command_name is required but was not found"
    fi
done

if [ "${BASH_VERSINFO[0]}" -ge 4 ]; then
    pass "Bash ${BASH_VERSION} satisfies the Bash 4+ requirement"
else
    fail "Bash 4+ is required (found ${BASH_VERSION})"
fi

if command -v codex >/dev/null 2>&1 || command -v claude >/dev/null 2>&1; then
    pass "at least one supported agent CLI is installed"
else
    warn "neither Codex nor Claude was found; tmux works but agent tracking cannot be exercised"
fi

if grep -Fq '# >>> muxpager managed configuration >>>' "$HOME/.tmux.conf" 2>/dev/null; then
    pass "tmux loads the managed project configuration"
else
    fail "~/.tmux.conf does not contain the managed project block"
fi

for hook_file in "$HOME/.codex/hooks.json" "$HOME/.claude/settings.json"; do
    if grep -Fq '/.local/libexec/muxpager/hooks/' "$hook_file" 2>/dev/null; then
        pass "$hook_file contains managed agent hooks"
    else
        fail "$hook_file does not contain managed agent hooks"
    fi
done

if [ "$(readlink -f "$HOME/.local/bin/tmux-telegram-bridge" 2>/dev/null)" = \
    "$PROJECT_ROOT/telegram/bridge.py" ]; then
    pass "Telegram bridge launcher points at this checkout"
else
    fail "Telegram bridge launcher is missing or points elsewhere"
fi

if tmux list-sessions >/dev/null 2>&1; then
    configured_root="$(
        tmux show-environment -g MUXPAGER_ROOT 2>/dev/null |
            sed 's/^[^=]*=//'
    )"
    if [ "$configured_root" = "$PROJECT_ROOT" ]; then
        pass "the running tmux server uses this checkout"
    else
        fail "the running tmux server uses '${configured_root:-no project root}'"
    fi
    if python3 "$PROJECT_ROOT/telegram/bridge.py" --check >/dev/null 2>&1; then
        pass "agent pane discovery is operational"
    else
        fail "agent pane discovery failed"
    fi
else
    warn "no tmux server is running; live configuration and pane discovery were not checked"
fi

ENV_FILE="$CONFIG_HOME/tmux-telegram-bridge/env"
if grep -Eq '^TMUX_TELEGRAM_BOT_TOKEN=[^[:space:]].*$' "$ENV_FILE" 2>/dev/null; then
    pass "Telegram bot token is configured"
    if systemctl --user is-active --quiet tmux-telegram-bridge.service; then
        pass "Telegram bridge service is active"
    else
        fail "Telegram token is configured but the bridge service is not active"
    fi
else
    warn "Telegram bot token is not configured yet"
fi

STATE_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/tmux-telegram-bridge/state.json"
if python3 - "$STATE_FILE" <<'PY' >/dev/null 2>&1
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
raise SystemExit(0 if isinstance(payload.get("group_chat_id"), int) else 1)
PY
then
    pass "private Telegram topic group is paired"
else
    warn "private Telegram topic group has not been paired with /setup"
fi

if [ "$FAILURES" -eq 0 ]; then
    printf '\nAll required checks passed.\n'
    exit 0
fi
printf '\n%s required check(s) failed.\n' "$FAILURES"
exit 1
