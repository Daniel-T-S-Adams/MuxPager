#!/usr/bin/env bash

# Shared helpers for finding Claude/Codex/Devin processes inside tmux sessions.
# Uses an iterative BFS with a pre-built PID map to avoid recursive subshells.

# Global process snapshot, built once per short-lived script invocation. The
# sidebar collector is long-lived and calls reset_agent_process_cache before
# each full collection pass.
declare -A _AP_CHILDREN=()  # pid → space-separated child PIDs
declare -A _AP_ARGS=()      # pid → command args
declare -A _AP_STATE=()     # pid → ps state (R/S/D/I/T/Z/X...)
_AP_MAP_BUILT=0

reset_agent_process_cache() {
    _AP_MAP_BUILT=0
    _AP_CHILDREN=()
    _AP_ARGS=()
    _AP_STATE=()
}

_build_agent_pid_map() {
    (( _AP_MAP_BUILT )) && return
    _AP_MAP_BUILT=1
    _AP_CHILDREN=()
    _AP_ARGS=()
    _AP_STATE=()

    local snapshot has_state=1
    snapshot=$(ps -eo pid=,ppid=,stat=,args= 2>/dev/null || true)
    if [ -n "$snapshot" ]; then
        # Some minimal/fake ps implementations ignore the requested columns
        # and return the legacy pid/ppid/args shape. Detect that before parsing
        # the third token as a process state.
        local sample sample_rest sample_state
        sample="${snapshot%%$'\n'*}"
        sample="${sample#"${sample%%[![:space:]]*}"}"
        sample_rest="${sample#* }"
        sample_rest="${sample_rest#"${sample_rest%%[![:space:]]*}"}"
        sample_rest="${sample_rest#* }"
        sample_rest="${sample_rest#"${sample_rest%%[![:space:]]*}"}"
        sample_state="${sample_rest%% *}"
        local valid_state_re='^[RSDITtZKXxWPN<>+sLl]+$'
        if [[ ! "$sample_state" =~ $valid_state_re ]]; then
            has_state=0
            snapshot=$(ps -eo pid=,ppid=,args= 2>/dev/null || true)
        fi
    fi
    if [ -z "$snapshot" ]; then
        # Compatibility fallback for restricted/minimal ps implementations.
        # Unknown state remains usable so process detection still works.
        has_state=0
        snapshot=$(ps -eo pid=,ppid=,args= 2>/dev/null || true)
    fi

    local line pid ppid state args
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        pid="${line%% *}"
        line="${line#* }"
        line="${line#"${line%%[![:space:]]*}"}"
        ppid="${line%% *}"
        line="${line#* }"
        line="${line#"${line%%[![:space:]]*}"}"
        if (( has_state )); then
            state="${line%% *}"
            args="${line#* }"
            args="${args#"${args%%[![:space:]]*}"}"
        else
            state="?"
            args="$line"
        fi
        [ -z "$pid" ] && continue
        [ -z "$ppid" ] && continue
        _AP_ARGS[$pid]="$args"
        _AP_STATE[$pid]="$state"
        _AP_CHILDREN[$ppid]+="$pid "
    done <<< "$snapshot"
}

# R/S/D/I and similar states are all live. In particular, sleeping or
# uninterruptible-wait agents may simply be waiting for a long-running tool and
# must remain visible. Only terminal process states are rejected.
agent_process_state_is_usable() {
    case "${1:0:1}" in
        T|t|Z|X|x) return 1 ;;
        *)         return 0 ;;
    esac
}

agent_name_from_args() {
    local args="$1"
    local executable arg1 arg2 candidate
    read -r executable arg1 arg2 _ <<< "$args"
    candidate="${executable##*/}"

    # Support common script launchers without matching an arbitrary argument
    # such as `rg codex` or `bash -c claude` as an agent process.
    case "$candidate" in
        node|nodejs|bun|deno)
            candidate="${arg1##*/}"
            ;;
        env)
            candidate="${arg1##*/}"
            if [[ "$candidate" == "node" || "$candidate" == "nodejs" || "$candidate" == "bun" || "$candidate" == "deno" ]]; then
                candidate="${arg2##*/}"
            fi
            ;;
    esac

    # Claude Code's native installer stores each release as
    # .../claude/versions/<version> behind a `claude` symlink. A process started
    # through the resolved path has the version number as its basename. Only a
    # file directly inside that directory is renamed; every other path keeps
    # the basename rule above.
    case "${executable%/*}" in
        */claude/versions) candidate="claude" ;;
    esac

    case "$candidate" in
        claude|codex|devin) echo "$candidate" ;;
        *) return 1 ;;
    esac
}

agent_name_for_process() {
    local pid="$1"
    _build_agent_pid_map
    agent_name_from_args "${_AP_ARGS[$pid]:-}"
}

# Print the Unix time at which a process started, read from the kernel's own
# record: boot time plus the start offset in /proc/<pid>/stat.
#
# Do not use the mtime of the /proc/<pid> directory for this. That is the time
# the kernel created the procfs inode, not the time the process started. It
# resets whenever the inode is evicted and looked up again, and each procfs
# instance has its own copy -- a sandboxed systemd service therefore sees a
# much later "start" than a login shell does for the same long-lived process.
#
# Returns 1 when the start time cannot be determined (non-Linux, a process that
# already exited, or a restricted procfs).
agent_process_start_epoch() {
    local pid="$1"
    local proc_root="${2:-/proc}"
    local stat_file="$proc_root/$pid/stat"
    local boot_file="$proc_root/stat"
    local stat_line key value boot_time="" start_ticks ticks_per_second
    local -a fields

    [ -r "$stat_file" ] && [ -r "$boot_file" ] || return 1

    IFS= read -r stat_line < "$stat_file" || [ -n "$stat_line" ] || return 1
    # The command name is wrapped in parentheses and may itself contain spaces
    # or parentheses, so count fields from after the final ")". The state is
    # then field 3 and starttime (field 22) is the twentieth remaining field.
    read -r -a fields <<< "${stat_line##*) }"
    start_ticks="${fields[19]:-}"
    [[ "$start_ticks" =~ ^[0-9]+$ ]] || return 1

    while read -r key value _; do
        if [ "$key" = "btime" ]; then
            boot_time="$value"
            break
        fi
    done < "$boot_file"
    [[ "$boot_time" =~ ^[0-9]+$ ]] || return 1

    ticks_per_second=$(getconf CLK_TCK 2>/dev/null || true)
    [[ "$ticks_per_second" =~ ^[1-9][0-9]*$ ]] || ticks_per_second=100

    printf '%s\n' "$(( boot_time + start_ticks / ticks_per_second ))"
}

find_matching_descendant_pid() {
    local root_pid="$1"
    local pattern="${2:-claude|codex|devin}"

    _build_agent_pid_map

    # BFS using a simple queue (array + index)
    local queue=("$root_pid")
    local qi=0
    while (( qi < ${#queue[@]} )); do
        local cur="${queue[$qi]}"
        ((qi++))
        local cur_args="${_AP_ARGS[$cur]:-}"
        local detected_name=""
        [ -n "$cur_args" ] && detected_name=$(agent_name_from_args "$cur_args" 2>/dev/null || true)
        if [ -n "$detected_name" ] && [[ "$detected_name" =~ ^($pattern)$ ]]; then
            if agent_process_state_is_usable "${_AP_STATE[$cur]:-?}"; then
                echo "$cur"
                return 0
            fi

            # A stopped/dead agent may leave live helper descendants behind.
            # Do not mistake those helpers for an independently active agent.
            continue
        fi
        # Enqueue children
        local children="${_AP_CHILDREN[$cur]:-}"
        if [ -n "$children" ]; then
            for child in $children; do
                queue+=("$child")
            done
        fi
    done
    return 1
}

find_session_agent_pid() {
    local session="$1"
    local pattern="${2:-claude|codex|devin}"

    _build_agent_pid_map

    local pane_pids
    # -s covers every window in the session, not only its current window.
    pane_pids=$(tmux list-panes -t "$session" -s -F "#{pane_pid}" 2>/dev/null || true)
    if [ -z "$pane_pids" ]; then
        pane_pids=$(tmux list-panes -t "$session" -F "#{pane_pid}" 2>/dev/null) || return 1
    fi

    local pane_pid
    while IFS= read -r pane_pid; do
        [ -z "$pane_pid" ] && continue
        local match_pid
        match_pid=$(find_matching_descendant_pid "$pane_pid" "$pattern")
        if [ -n "$match_pid" ]; then
            echo "$match_pid"
            return 0
        fi
    done <<< "$pane_pids"

    return 1
}

find_pane_agent_pid() {
    local pane_target="$1"
    local pane_pid=""

    pane_pid=$(tmux display-message -p -t "$pane_target" "#{pane_pid}" 2>/dev/null || true)
    if [ -z "$pane_pid" ]; then
        pane_pid=$(tmux list-panes -t "$pane_target" -F "#{pane_pid}" 2>/dev/null || true)
        pane_pid="${pane_pid%%$'\n'*}"
    fi
    [ -n "$pane_pid" ] || return 1

    find_matching_descendant_pid "$pane_pid"
}

pane_has_agent_process() {
    find_pane_agent_pid "$1" >/dev/null 2>&1
}

session_has_agent_process() {
    local session="$1"
    local pattern="${2:-claude|codex|devin}"

    find_session_agent_pid "$session" "$pattern" >/dev/null 2>&1
}
