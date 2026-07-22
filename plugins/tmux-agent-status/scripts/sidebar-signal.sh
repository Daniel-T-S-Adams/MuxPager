#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/session-status.sh"
source "$SCRIPT_DIR/lib/sidebar-clients.sh"

case "${1:-refresh}" in
    collect)
        write_sidebar_refresh_token
        tmux run-shell -b "$SCRIPT_DIR/sidebar-collector.sh" 2>/dev/null || true
        ;;
    active)
        signal_sidebar_clients USR2 active
        ;;
    refresh|*)
        signal_sidebar_clients USR1 all
        ;;
esac
