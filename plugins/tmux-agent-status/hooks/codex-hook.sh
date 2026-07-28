#!/usr/bin/env bash

# Codex hook for tmux-agent-status.
# Hook events are passed as the first argument by the configured Codex command
# hook. The JSON payload also contains the exact final assistant response for a
# Stop event; cache that response for phone/notification integrations.

STATUS_DIR="$HOME/.cache/tmux-agent-status"
PARKED_DIR="$STATUS_DIR/parked"
PANE_DIR="$STATUS_DIR/panes"
REFRESH_FILE="$STATUS_DIR/.sidebar-refresh"
mkdir -p "$STATUS_DIR" "$PARKED_DIR" "$PANE_DIR"
[ -f "$REFRESH_FILE" ] || : > "$REFRESH_FILE"

# Read the JSON payload from stdin so Codex can close the hook cleanly.
HOOK_JSON="$(cat 2>/dev/null || true)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=cache-last-message.sh
source "$SCRIPT_DIR/cache-last-message.sh"
# shellcheck source=cache-user-prompt.sh
source "$SCRIPT_DIR/cache-user-prompt.sh"

in_remote_session() {
    [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]
}

get_tmux_session() {
    local tmux_session=""

    if [ -n "${TMUX:-}" ] || in_remote_session; then
        tmux_session=$(tmux display-message -p '#{session_name}' 2>/dev/null)

        if [ -z "$tmux_session" ]; then
            if in_remote_session; then
                case "$(hostname -s 2>/dev/null)" in
                    instance-*) tmux_session="reachgpu" ;;
                    keen-schrodinger) tmux_session="sd1" ;;
                    sam-l4-workstation-image) tmux_session="l4-workstation" ;;
                    persistent-faraday) tmux_session="tig" ;;
                    instance-20250620-122051) tmux_session="reachgpu" ;;
                    *) tmux_session=$(hostname -s 2>/dev/null) ;;
                esac
            elif [ -n "${TMUX:-}" ]; then
                local socket_path="${TMUX%%,*}"
                tmux_session=$(basename "$socket_path")
            fi
        fi
    fi

    [ -n "$tmux_session" ] || return 1
    printf '%s\n' "$tmux_session"
}

set_status() {
    local tmux_session="$1"
    local requested_status="$2"
    local session_status="$requested_status"
    local status_file="$STATUS_DIR/${tmux_session}.status"
    local remote_status_file="$STATUS_DIR/${tmux_session}-remote.status"

    if [ -n "${TMUX_PANE:-}" ]; then
        local pane_file="$PANE_DIR/${tmux_session}_${TMUX_PANE}.status"
        local agent_file="$PANE_DIR/${tmux_session}_${TMUX_PANE}.agent"
        echo "$requested_status" > "$pane_file"
        echo "codex" > "$agent_file"

        session_status="done"
        local existing_pane_file=""
        for existing_pane_file in "$PANE_DIR/${tmux_session}_"*.status; do
            [ -f "$existing_pane_file" ] || continue

            local pane_status=""
            pane_status=$(cat "$existing_pane_file" 2>/dev/null || echo "")
            case "$pane_status" in
                working)
                    session_status="working"
                    break
                    ;;
            esac
        done
    fi

    echo "$session_status" > "$status_file"
    if in_remote_session; then
        echo "$session_status" > "$remote_status_file" 2>/dev/null
    fi
}

clear_interaction_overrides() {
    local tmux_session="$1"
    local session_parked_file="$PARKED_DIR/${tmux_session}.parked"

    if [ -f "$session_parked_file" ]; then
        rm -f "$session_parked_file" "$PARKED_DIR/${tmux_session}_"*.parked 2>/dev/null
    elif [ -n "${TMUX_PANE:-}" ]; then
        rm -f "$PARKED_DIR/${tmux_session}_${TMUX_PANE}.parked"
    fi
}

mark_refresh() {
    printf '%s:%s:%s\n' "$(date +%s)" "$$" "${RANDOM:-0}" > "$REFRESH_FILE" 2>/dev/null || true
}

TMUX_SESSION=$(get_tmux_session) || exit 0
HOOK_TYPE="${1:-}"
PARKED_FILE="$PARKED_DIR/${TMUX_SESSION}.parked"

case "$HOOK_TYPE" in
    SessionStart)
        if [ ! -f "$PARKED_FILE" ]; then
            set_status "$TMUX_SESSION" "done"
            mark_refresh
        fi
        ;;
    UserPromptSubmit)
        cache_local_user_prompt "$HOOK_JSON" "$TMUX_SESSION" "${TMUX_PANE:-}"
        python3 \
            "$HOME/.local/libexec/muxpager/questions/mark_local_answer.py" \
            codex >/dev/null 2>&1 || true
        clear_interaction_overrides "$TMUX_SESSION"
        set_status "$TMUX_SESSION" "working"
        mark_refresh
        ;;
    PreToolUse|PostToolUse)
        if [ ! -f "$PARKED_FILE" ]; then
            set_status "$TMUX_SESSION" "working"
        fi
        mark_refresh
        ;;
    Stop)
        cache_last_assistant_message "$HOOK_JSON" "$TMUX_SESSION" "${TMUX_PANE:-}"
        set_status "$TMUX_SESSION" "done"
        mark_refresh
        ;;
esac

exit 0
