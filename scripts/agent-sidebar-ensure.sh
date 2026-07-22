#!/usr/bin/env bash
#
# Idempotent "watchdog" for the tmux-agent-status sidebar.
#
# The plugin's sidebar.sh is an interactive switcher that EXITS itself on Enter,
# 'q', or loss of its tty -- and with remain-on-exit off, the pane is destroyed.
# That's why the sidebar "disappears". This script makes it a permanent fixture:
# it ensures the target window has a sidebar, creating one ONLY if absent, and
# never steals focus from the pane you're actually working in.
#
# Usage: agent-sidebar-ensure.sh [target-window]   (defaults to current window)

# Locate the vendored plugin relative to this script (repo-portable).
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../plugins/tmux-agent-status/scripts" 2>/dev/null && pwd)"
SIDEBAR_TITLE="agent-sidebar"
TARGET="${1:-}"

LOCK_NAME="agent-sidebar-ensure"
tmux wait-for -L "$LOCK_NAME" 2>/dev/null || exit 0
unlock() {
    tmux wait-for -U "$LOCK_NAME" 2>/dev/null || true
}
trap unlock EXIT

# Bail quietly if the plugin isn't installed.
[ -n "$PLUGIN_DIR" ] && [ -x "$PLUGIN_DIR/sidebar.sh" ] || exit 0

# Configured width (matches the plugin default).
width=$(tmux show-option -gqv "@agent-sidebar-width" 2>/dev/null)
[ -z "$width" ] && width=42

target_flag=()
[ -n "$TARGET" ] && target_flag=(-t "$TARGET")

# Already have a sidebar in this window? Then there's nothing to do.
existing=$(tmux list-panes "${target_flag[@]}" -F '#{pane_id} #{pane_title}' 2>/dev/null \
    | awk -v t="$SIDEBAR_TITLE" '$2 == t { print $1; exit }')
[ -n "$existing" ] && exit 0

# Remember which pane is active so we can hand focus back afterward.
active=$(tmux list-panes "${target_flag[@]}" -F '#{pane_active} #{pane_id}' 2>/dev/null \
    | awk '$1 == "1" { print $2; exit }')

# Create a left-edge split running the sidebar.
leftmost=$(tmux list-panes "${target_flag[@]}" -F '#{pane_left} #{pane_id}' 2>/dev/null \
    | sort -n | head -1 | awk '{print $2}')
[ -z "$leftmost" ] && exit 0

new_pane=$(tmux split-window -hbl "$width" -t "$leftmost" -PF '#{pane_id}' "$PLUGIN_DIR/sidebar.sh" 2>/dev/null)
[ -z "$new_pane" ] && exit 0
tmux select-pane -t "$new_pane" -T "$SIDEBAR_TITLE"

# Restore focus to the user's original pane.
[ -n "$active" ] && tmux select-pane -t "$active"
