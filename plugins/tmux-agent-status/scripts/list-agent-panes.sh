#!/usr/bin/env bash

# Print one tab-separated row for every live local agent pane:
# pane_id, session, window_index, window_name, pane_index, agent, status,
# conversation_id.
# Agent liveness and pane status use the same helpers as the sidebar/switcher.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/session-status.sh
source "$SCRIPT_DIR/lib/session-status.sh"

reset_agent_process_cache

tab=$'\t'
rows=$(tmux list-panes -a -F "#{pane_id}${tab}#{session_name}${tab}#{window_index}${tab}#{window_name}${tab}#{pane_index}${tab}#{pane_pid}${tab}#{pane_dead}${tab}#{pane_title}" 2>/dev/null || true)

while IFS=$'\t' read -r pane_id session window_index window_name pane_index pane_pid pane_dead pane_title; do
    [ -n "$pane_id" ] || continue
    [ "$pane_dead" = "0" ] || continue
    [ "$pane_title" != "agent-sidebar" ] || continue

    match_pid=$(find_matching_descendant_pid "$pane_pid" 2>/dev/null) || continue
    agent=$(agent_name_for_process "$match_pid" 2>/dev/null) || continue
    status=$(get_pane_status "$session" "$pane_id" 2>/dev/null || true)
    conversation_id=""

    identity_file="$PANE_DIR/${session}_${pane_id}.conversation"
    if [ -f "$identity_file" ]; then
        IFS= read -r cached_agent < "$identity_file" || cached_agent=""
        conversation_id=$(sed -n '2p' "$identity_file" 2>/dev/null || true)
        [ "$cached_agent" = "$agent" ] || conversation_id=""
    fi

    case "$status" in
        working|done|ask|parked) ;;
        *) status="done" ;;
    esac

    # tmux names should be single-line already; replace tabs defensively so the
    # output stays machine-readable if a name was created programmatically.
    session=${session//$'\t'/ }
    window_name=${window_name//$'\t'/ }
    agent=${agent//$'\t'/ }
    conversation_id=${conversation_id//$'\t'/ }
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$pane_id" "$session" "$window_index" "$window_name" \
        "$pane_index" "$agent" "$status" "$conversation_id"
done <<< "$rows"
