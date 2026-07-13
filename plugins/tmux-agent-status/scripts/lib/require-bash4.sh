#!/usr/bin/env bash

# tmux-agent-status uses associative arrays in its shared collection code.
# macOS still ships Bash 3.2 as /bin/bash, so re-exec plugin entrypoints with a
# modern Bash when one is available from Homebrew or other common package paths.

[[ -n "${_TMUX_AGENT_STATUS_REQUIRE_BASH4_LOADED:-}" ]] && return 0
_TMUX_AGENT_STATUS_REQUIRE_BASH4_LOADED=1

_tmux_agent_status_bash4_is_current() {
    [ -n "${BASH_VERSINFO:-}" ] && [ "${BASH_VERSINFO[0]}" -ge 4 ] 2>/dev/null
}

_tmux_agent_status_bash4_candidate_ok() {
    local candidate="$1"
    [ -n "$candidate" ] || return 1
    [ -x "$candidate" ] || return 1
    "$candidate" -c '[ "${BASH_VERSINFO[0]}" -ge 4 ]' >/dev/null 2>&1
}

_tmux_agent_status_find_bash4() {
    local candidate=""
    local seen=":"

    for candidate in \
        "${TMUX_AGENT_STATUS_BASH:-}" \
        "$(command -v bash 2>/dev/null || true)" \
        /opt/homebrew/bin/bash \
        /usr/local/bin/bash \
        /opt/local/bin/bash \
        /sw/bin/bash
    do
        [ -n "$candidate" ] || continue
        case "$seen" in
            *":$candidate:"*) continue ;;
        esac
        seen="${seen}${candidate}:"

        if _tmux_agent_status_bash4_candidate_ok "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

_tmux_agent_status_abs_path() {
    local path="$1"
    local dir=""
    local base=""

    case "$path" in
        /*)
            printf '%s\n' "$path"
            ;;
        */*)
            dir="$(cd "$(dirname "$path")" && pwd)" || return 1
            base="$(basename "$path")"
            printf '%s/%s\n' "$dir" "$base"
            ;;
        *)
            command -v -- "$path"
            ;;
    esac
}

require_bash4() {
    _tmux_agent_status_bash4_is_current && return 0

    local bash4=""
    bash4="$(_tmux_agent_status_find_bash4)" || {
        cat >&2 <<'EOF'
tmux-agent-status requires Bash 4 or newer.
macOS /bin/bash is Bash 3.2; install a newer Bash, for example:
  brew install bash
EOF
        exit 1
    }

    local stack_depth="${#BASH_SOURCE[@]}"
    if [ "$stack_depth" -lt 3 ]; then
        echo "tmux-agent-status: rerun this command with Bash 4 or newer: $bash4" >&2
        exit 1
    fi

    local script_index=$((stack_depth - 1))
    local script_path="${BASH_SOURCE[$script_index]}"
    script_path="$(_tmux_agent_status_abs_path "$script_path")" || {
        echo "tmux-agent-status: found Bash 4 at $bash4, but could not resolve the script path" >&2
        exit 1
    }

    exec "$bash4" "$script_path" "$@"
}
