#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMCTL_BIN="${TMUX_AGENT_SYSTEMCTL_BIN:-systemctl}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command not found: $1" >&2
        exit 1
    fi
}

require_command bash
require_command python3
require_command tmux
require_command fzf
require_command git
require_command ps
require_command pgrep
require_command "$SYSTEMCTL_BIN"

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "Bash 4 or newer is required (found ${BASH_VERSION})." >&2
    exit 1
fi

python3 "$PROJECT_ROOT/scripts/manage-user-config.py" \
    install --project-root "$PROJECT_ROOT"
"$PROJECT_ROOT/scripts/setup-runtime-links.sh"
python3 "$PROJECT_ROOT/scripts/manage-agent-mcp.py" install
TMUX_AGENT_SYSTEMCTL_BIN="$SYSTEMCTL_BIN" \
    "$PROJECT_ROOT/telegram/setup.sh"

if [ "${TMUX_AGENT_SKIP_LIVE_RELOAD:-0}" != "1" ] && tmux list-sessions >/dev/null 2>&1; then
    tmux source-file "$PROJECT_ROOT/.tmux.conf"
fi

ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-telegram-bridge/env"
if grep -Eq '^TMUX_TELEGRAM_BOT_TOKEN=[^[:space:]].*$' "$ENV_FILE"; then
    "$SYSTEMCTL_BIN" --user enable tmux-telegram-bridge.service
    "$SYSTEMCTL_BIN" --user restart tmux-telegram-bridge.service
else
    echo
    echo "Telegram credentials are not configured yet."
fi

echo
echo "MuxPager installed from: $PROJECT_ROOT"
echo "Core configuration: ~/.tmux.conf, ~/.codex/hooks.json, ~/.claude/settings.json"
echo "Choice questions: muxpager MCP registered for each installed agent CLI"
echo "Telegram configuration: $ENV_FILE"
echo
echo "Restart tmux plus any running Codex/Claude processes to give every pane the"
echo "new managed environment. Then run: $PROJECT_ROOT/scripts/doctor.sh"
