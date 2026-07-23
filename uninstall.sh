#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMCTL_BIN="${TMUX_AGENT_SYSTEMCTL_BIN:-systemctl}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

safe_remove_link() {
    local path="$1"
    local expected="$2"
    if [ ! -L "$path" ]; then
        return
    fi
    if [ "$(readlink -f "$path")" != "$(readlink -f "$expected")" ]; then
        echo "Preserving link managed by something else: $path" >&2
        return
    fi
    rm -f "$path"
}

"$SYSTEMCTL_BIN" --user disable --now tmux-telegram-bridge.service >/dev/null 2>&1 || true
python3 "$PROJECT_ROOT/scripts/manage-agent-mcp.py" uninstall
python3 "$PROJECT_ROOT/scripts/manage-user-config.py" uninstall

safe_remove_link \
    "$CONFIG_HOME/systemd/user/tmux-telegram-bridge.service" \
    "$PROJECT_ROOT/telegram/tmux-telegram-bridge.service"
safe_remove_link \
    "$HOME/.local/bin/tmux-telegram-bridge" \
    "$PROJECT_ROOT/telegram/bridge.py"
safe_remove_link \
    "$HOME/.local/bin/muxpager-choice-server" \
    "$PROJECT_ROOT/questions/mcp_server.py"
safe_remove_link \
    "$HOME/.local/libexec/muxpager/hooks" \
    "$PROJECT_ROOT/plugins/tmux-agent-status/hooks"
safe_remove_link \
    "$HOME/.local/libexec/muxpager/questions" \
    "$PROJECT_ROOT/questions"
safe_remove_link \
    "$HOME/.local/libexec/tmux-agent-console/hooks" \
    "$PROJECT_ROOT/plugins/tmux-agent-status/hooks"
"$SYSTEMCTL_BIN" --user daemon-reload

echo "Managed tmux, Codex, Claude, runtime links, and the Telegram service were removed."
echo "Private Telegram credentials, topic state, and choice history were preserved."
echo "Restart tmux and any agent processes to clear their existing runtime environment."
