#!/usr/bin/env bash

# Pick any nearby Git repository, then create a named tmux session with the
# repository-isolated worktree behavior implemented by new-session.sh.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEW_SESSION_HELPER="$PROJECT_ROOT/scripts/new-session.sh"
START_CWD="$PWD"
WORKSPACE_ROOT="${MUXPAGER_REPOSITORY_ROOT:-}"
SELECTED_REPO=""
SESSION_NAME=""
LIST_ONLY=0

usage() {
    cat <<'EOF'
Usage:
  new-session-picker.sh [--cwd DIRECTORY] [--workspace-root DIRECTORY]
  new-session-picker.sh --repo REPOSITORY --name SESSION
  new-session-picker.sh --list [--workspace-root DIRECTORY]

Without --repo or --name, fzf selects a nearby Git repository and the script
then prompts for the new tmux session name.
EOF
}

fail() {
    printf 'MuxPager: %s\n' "$1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --cwd)
            [ "$#" -ge 2 ] || fail "--cwd requires a value"
            START_CWD="$2"
            shift 2
            ;;
        --workspace-root)
            [ "$#" -ge 2 ] || fail "--workspace-root requires a value"
            WORKSPACE_ROOT="$2"
            shift 2
            ;;
        --repo)
            [ "$#" -ge 2 ] || fail "--repo requires a value"
            SELECTED_REPO="$2"
            shift 2
            ;;
        --name)
            [ "$#" -ge 2 ] || fail "--name requires a value"
            SESSION_NAME="$2"
            shift 2
            ;;
        --list)
            LIST_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $1"
            ;;
    esac
done

[ -d "$START_CWD" ] || fail "starting directory does not exist: $START_CWD"
START_CWD=$(cd "$START_CWD" 2>/dev/null && pwd -P) || \
    fail "cannot access starting directory: $START_CWD"

current_repo=$(git -C "$START_CWD" rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$WORKSPACE_ROOT" ]; then
    if [ -n "${MUXPAGER_ROOT:-}" ] && [ -d "$MUXPAGER_ROOT" ]; then
        WORKSPACE_ROOT=$(dirname "$MUXPAGER_ROOT")
    elif [ -n "$current_repo" ]; then
        WORKSPACE_ROOT=$(dirname "$current_repo")
    else
        WORKSPACE_ROOT="$START_CWD"
    fi
fi
[ -d "$WORKSPACE_ROOT" ] || fail "repository search root does not exist: $WORKSPACE_ROOT"
WORKSPACE_ROOT=$(cd "$WORKSPACE_ROOT" 2>/dev/null && pwd -P) || \
    fail "cannot access repository search root: $WORKSPACE_ROOT"

declare -A seen_repositories=()
repositories=()

add_repository() {
    local candidate="$1" primary="" canonical=""
    [ -d "$candidate" ] || return 0
    [ "$(git -C "$candidate" rev-parse --is-inside-work-tree 2>/dev/null || true)" = true ] || return 0

    while IFS= read -r line; do
        case "$line" in
            'worktree '*)
                primary="${line#worktree }"
                break
                ;;
        esac
    done < <(git -C "$candidate" worktree list --porcelain 2>/dev/null || true)
    [ -n "$primary" ] || primary=$(git -C "$candidate" rev-parse --show-toplevel 2>/dev/null || true)
    [ -d "$primary" ] || return 0
    canonical=$(cd "$primary" 2>/dev/null && pwd -P) || return 0

    if [ -z "${seen_repositories[$canonical]+set}" ]; then
        seen_repositories[$canonical]=1
        repositories+=("$canonical")
    fi
}

[ -n "$current_repo" ] && add_repository "$current_repo"
while IFS= read -r git_marker; do
    add_repository "$(dirname "$git_marker")"
done < <(
    find "$WORKSPACE_ROOT" -mindepth 1 -maxdepth 5 \
        \( -type d -name .muxpager -prune \) -o \
        \( -name .git -print -prune \) 2>/dev/null
)

if [ "${#repositories[@]}" -gt 0 ]; then
    mapfile -t repositories < <(printf '%s\n' "${repositories[@]}" | LC_ALL=C sort -u)
fi

if [ "$LIST_ONLY" -eq 1 ]; then
    printf '%s\n' "${repositories[@]}"
    exit 0
fi

if [ -z "$SELECTED_REPO" ]; then
    [ "${#repositories[@]}" -gt 0 ] || fail "no Git repositories found under $WORKSPACE_ROOT"
    command -v fzf >/dev/null 2>&1 || fail "fzf is required to choose a repository"
    SELECTED_REPO=$(
        printf '%s\n' "${repositories[@]}" |
            fzf \
                --layout=reverse \
                --border \
                --info=inline \
                --prompt='Repository > ' \
                --header='Type to filter, then Enter to choose a Git repository'
    ) || exit 0
fi

[ -d "$SELECTED_REPO" ] || fail "selected repository does not exist: $SELECTED_REPO"
SELECTED_REPO=$(cd "$SELECTED_REPO" 2>/dev/null && pwd -P) || \
    fail "cannot access selected repository: $SELECTED_REPO"
[ "$(git -C "$SELECTED_REPO" rev-parse --is-inside-work-tree 2>/dev/null || true)" = true ] || \
    fail "selected directory is not a Git repository: $SELECTED_REPO"

if [ -z "$SESSION_NAME" ]; then
    printf '\nSelected repository: %s\nNew session name: ' "$SELECTED_REPO"
    IFS= read -r SESSION_NAME || exit 0
fi
[ -n "$SESSION_NAME" ] || exit 0

if [ -n "${TMUX:-}" ] && [ -t 0 ]; then
    if ! "$NEW_SESSION_HELPER" --name "$SESSION_NAME" --cwd "$SELECTED_REPO"; then
        printf '\nPress Enter to close...'
        IFS= read -r _ || true
        exit 0
    fi
else
    exec "$NEW_SESSION_HELPER" --name "$SESSION_NAME" --cwd "$SELECTED_REPO"
fi
