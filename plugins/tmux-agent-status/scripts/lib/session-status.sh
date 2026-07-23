#!/usr/bin/env bash

# Shared session-status helpers used by the sidebar, switcher, and other scripts.
# Provides: is_ssh_session, has_agent_in_session, session_is_fully_parked,
#           status_priority, get_pane_status, get_window_status, get_agent_status,
#           sync_session_after_child_scope_change, plus status-directory constants.

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=require-bash4.sh
source "$_LIB_DIR/require-bash4.sh"
require_bash4 "$@"

[[ -n "${_SESSION_STATUS_LOADED:-}" ]] && return 0
_SESSION_STATUS_LOADED=1

STATUS_DIR="$HOME/.cache/tmux-agent-status"
PARKED_DIR="$STATUS_DIR/parked"
LEGACY_WAIT_DIR="$STATUS_DIR/wait"
PANE_DIR="$STATUS_DIR/panes"
SIDEBAR_CLIENT_DIR="$STATUS_DIR/sidebar-clients"
STATUS_LINE_CACHE_FILE="$STATUS_DIR/.status-line"
STATUS_LINE_COUNTS_FILE="$STATUS_DIR/.status-line-counts"
REFRESH_FILE="$STATUS_DIR/.sidebar-refresh"
mkdir -p "$STATUS_DIR" "$PARKED_DIR" "$PANE_DIR" "$SIDEBAR_CLIENT_DIR"
[ -f "$REFRESH_FILE" ] || : > "$REFRESH_FILE"

write_sidebar_refresh_token() {
    # Content, rather than second-resolution mtime, makes rapid working→done
    # transitions visible even when both hooks run within the same second.
    printf '%s:%s:%s\n' "$(date +%s)" "$$" "${RANDOM:-0}" > "$REFRESH_FILE" 2>/dev/null || true
}

migrate_removed_wait_state() {
    local status_file=""

    for status_file in "$STATUS_DIR"/*.status "$PANE_DIR"/*.status; do
        [ -f "$status_file" ] || continue
        if [ "$(cat "$status_file" 2>/dev/null || echo "")" = "wait" ]; then
            echo "done" > "$status_file" 2>/dev/null
        fi
    done

    if [ -d "$LEGACY_WAIT_DIR" ]; then
        rm -f "$LEGACY_WAIT_DIR"/*.wait 2>/dev/null
        rmdir "$LEGACY_WAIT_DIR" 2>/dev/null || true
    fi
}

# Earlier MuxPager versions offered a timed manual snooze. Normalize any
# leftover markers once so an upgraded sidebar cannot remain stuck on ⏸.
migrate_removed_wait_state

# Source process-detection helpers from the same lib directory.
# shellcheck source=agent-processes.sh
source "$_LIB_DIR/agent-processes.sh"

is_ssh_session() {
    local session="$1"
    if tmux list-panes -t "$session" -F "#{pane_current_command}" 2>/dev/null | grep -q "^ssh$"; then
        return 0
    fi
    case "$session" in
        reachgpu) return 0 ;;
        *) return 1 ;;
    esac
}

has_agent_in_session() {
    session_has_agent_process "$1"
}

session_is_fully_parked() {
    local session="$1"
    local live_pane=1
    local pane_session=""
    local pane_id=""

    while IFS=$'\t' read -r pane_session pane_id; do
        [ "$pane_session" = "$session" ] || continue
        [ -n "$pane_id" ] || continue
        live_pane=0
        [ -f "$PARKED_DIR/${session}_${pane_id}.parked" ] || return 1
    done < <(tmux list-panes -a -F '#{session_name}'$'\t''#{pane_id}' 2>/dev/null)

    if [ "$live_pane" -eq 0 ]; then
        return 0
    fi

    local seen_pane=1
    local pane_file=""

    for pane_file in "$PANE_DIR/${session}_"*.status; do
        [ -f "$pane_file" ] || continue
        seen_pane=0

        local pane_id
        pane_id=$(basename "$pane_file" .status)
        pane_id="${pane_id#${session}_}"
        [ -f "$PARKED_DIR/${session}_${pane_id}.parked" ] || return 1
    done

    if [ "$seen_pane" -eq 0 ]; then
        return 0
    fi

    [ -f "$PARKED_DIR/${session}.parked" ]
}

status_priority() {
    case "$1" in
        working) echo 4 ;;
        ask) echo 3 ;;
        done) echo 2 ;;
        parked) echo 1 ;;
        *) echo 0 ;;
    esac
}

write_session_status() {
    local session="$1"
    local state="$2"
    local remote_status="$STATUS_DIR/${session}-remote.status"

    if [ -f "$remote_status" ] && is_ssh_session "$session"; then
        echo "$state" > "$remote_status" 2>/dev/null
    else
        [ -f "$remote_status" ] && ! is_ssh_session "$session" && rm -f "$remote_status" 2>/dev/null
        echo "$state" > "$STATUS_DIR/${session}.status" 2>/dev/null
    fi
}

recompute_session_status() {
    local session="$1"
    local best_status="done"
    local best_priority
    best_priority=$(status_priority "$best_status")

    if session_is_fully_parked "$session"; then
        write_session_status "$session" "parked"
        return
    fi

    local pane_file=""
    for pane_file in "$PANE_DIR/${session}_"*.status; do
        [ -f "$pane_file" ] || continue

        local pane_name pane_id pane_status pane_priority
        pane_name=$(basename "$pane_file" .status)
        pane_id="${pane_name##*_}"

        if [ -f "$PARKED_DIR/${session}_${pane_id}.parked" ]; then
            pane_status="parked"
        else
            pane_status=$(cat "$pane_file" 2>/dev/null || echo "")
        fi

        pane_priority=$(status_priority "$pane_status")
        if [ "$pane_priority" -gt "$best_priority" ]; then
            best_priority="$pane_priority"
            best_status="$pane_status"
        fi
    done

    write_session_status "$session" "$best_status"
}

sync_session_after_child_scope_change() {
    local session="$1"

    # Lower-scope actions should not leave stale session-wide overrides behind.
    rm -f "$PARKED_DIR/${session}.parked"

    if session_is_fully_parked "$session"; then
        write_session_status "$session" "parked"
        return
    fi

    recompute_session_status "$session"
}

get_pane_status() {
    local session="$1"
    local pane_id="$2"
    local pane_status="$PANE_DIR/${session}_${pane_id}.status"
    local pane_parked="$PARKED_DIR/${session}_${pane_id}.parked"

    if [ -f "$pane_parked" ]; then
        echo "parked"
        return
    fi

    if [ -f "$pane_status" ]; then
        cat "$pane_status" 2>/dev/null || echo ""
        return
    fi

    # A local pane without pane-level metadata must not inherit the status of
    # an agent in another window. Only inherit session state when this pane has
    # its own usable agent process. Remote SSH status remains session-scoped.
    if is_ssh_session "$session" || pane_has_agent_process "$pane_id"; then
        get_agent_status "$session"
    else
        echo ""
    fi
}

get_window_status() {
    local session="$1"
    local window_index="$2"
    local best_status=""
    local best_priority=0
    local pane_id=""

    while IFS= read -r pane_id; do
        [ -z "$pane_id" ] && continue

        local pane_status pane_priority
        pane_status=$(get_pane_status "$session" "$pane_id")
        pane_priority=$(status_priority "$pane_status")
        if [ "$pane_priority" -gt "$best_priority" ]; then
            best_priority="$pane_priority"
            best_status="$pane_status"
        fi
    done < <(tmux list-panes -t "${session}:${window_index}" -F "#{pane_id}" 2>/dev/null)

    if [ -n "$best_status" ]; then
        echo "$best_status"
    else
        get_agent_status "$session"
    fi
}

get_agent_status() {
    local session="$1"

    if session_is_fully_parked "$session"; then
        echo "parked"
        return
    fi

    # Check for remote status file first (for SSH sessions)
    local remote_status="$STATUS_DIR/${session}-remote.status"
    if [ -f "$remote_status" ] && is_ssh_session "$session"; then
        local remote_value=""
        remote_value=$(cat "$remote_status" 2>/dev/null || echo "")
        if [ "$remote_value" = "wait" ]; then
            remote_value="done"
            echo "done" > "$remote_status" 2>/dev/null
        fi
        printf '%s\n' "$remote_value"
        return
    elif [ -f "$remote_status" ] && ! is_ssh_session "$session"; then
        rm -f "$remote_status" 2>/dev/null
    fi

    # Check local status files
    local status_file="$STATUS_DIR/${session}.status"
    if [ -f "$status_file" ]; then
        local status
        status=$(cat "$status_file" 2>/dev/null || echo "")
        if [ "$status" = "parked" ] && ! session_is_fully_parked "$session"; then
            recompute_session_status "$session"
            status=$(cat "$status_file" 2>/dev/null || echo "")
        fi
        printf '%s\n' "$status"
    else
        echo ""
    fi
}

get_ssh_host() {
    local session="$1"
    if is_ssh_session "$session"; then
        echo "$session"
    fi
}
