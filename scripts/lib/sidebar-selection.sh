#!/usr/bin/env bash

# Helpers for keeping the sidebar cursor aligned with the active tmux target.

[[ -n "${_SIDEBAR_SELECTION_LOADED:-}" ]] && return 0
_SIDEBAR_SELECTION_LOADED=1

sidebar_active_selection_index() {
    local current_session="$1"
    local current_pane="$2"
    local current_window="$3"

    [[ -n "$current_session" ]] || return 1

    local i sel_name sel_type sel_session token
    local window_idx=-1
    local session_idx=-1

    for ((i=SESS_START; i<SEL_COUNT; i++)); do
        sel_name="${SEL_NAMES[$i]:-}"
        sel_type="${SEL_TYPES[$i]:-}"

        case "$sel_type" in
            P)
                sel_session="${sel_name%%:*}"
                token="${sel_name#*:}"
                [[ "$sel_session" == "$current_session" ]] || continue

                if [[ -n "$current_pane" && "$token" == "$current_pane" ]]; then
                    printf '%s\n' "$i"
                    return 0
                fi

                if [[ -n "$current_window" && "$token" == "w$current_window" && "$window_idx" -lt 0 ]]; then
                    window_idx="$i"
                fi
                ;;
            S|W)
                if [[ "$sel_name" == "$current_session" && "$session_idx" -lt 0 ]]; then
                    session_idx="$i"
                fi
                ;;
        esac
    done

    if (( window_idx >= 0 )); then
        printf '%s\n' "$window_idx"
        return 0
    fi

    if (( session_idx >= 0 )); then
        printf '%s\n' "$session_idx"
        return 0
    fi

    return 1
}
