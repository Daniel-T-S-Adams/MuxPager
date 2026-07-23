#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
LIBEXEC_DIR="$HOME/.local/libexec/muxpager"
LEGACY_HOOKS_LINK="$HOME/.local/libexec/tmux-agent-console/hooks"
BRIDGE_LINK="$BIN_DIR/tmux-telegram-bridge"
HOOKS_LINK="$LIBEXEC_DIR/hooks"

safe_link() {
    local source_path="$1"
    local target_path="$2"

    if [ -e "$target_path" ] && [ ! -L "$target_path" ]; then
        echo "Refusing to replace non-symlink path: $target_path" >&2
        return 1
    fi
    ln -sfn "$source_path" "$target_path"
}

install -d -m 755 "$BIN_DIR"
install -d -m 755 "$LIBEXEC_DIR"
safe_link "$PROJECT_ROOT/telegram/bridge.py" "$BRIDGE_LINK"
safe_link "$PROJECT_ROOT/plugins/tmux-agent-status/hooks" "$HOOKS_LINK"

if [ -L "$LEGACY_HOOKS_LINK" ] && \
    [ "$(readlink -f "$LEGACY_HOOKS_LINK")" = \
      "$PROJECT_ROOT/plugins/tmux-agent-status/hooks" ]; then
    rm -f "$LEGACY_HOOKS_LINK"
fi
