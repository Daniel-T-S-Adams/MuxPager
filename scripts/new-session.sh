#!/usr/bin/env bash

# Create a detached tmux session from the current pane. Inside a Git checkout,
# give the session its own branch and worktree so parallel sessions cannot
# modify one another's working tree. Outside Git, preserve the ordinary tmux
# behavior while starting in the source pane's exact directory.

set -uo pipefail

TMUX_BIN="${MUXPAGER_TMUX_BIN:-tmux}"
SOURCE_PANE="${TMUX_PANE:-}"
SOURCE_SESSION=""
SESSION_NAME=""
SOURCE_CWD=""
PROMPT_OPTION="@muxpager-new-session-name"
PROMPT_CWD_OPTION="@muxpager-new-session-cwd"

usage() {
    cat <<'EOF'
Usage:
  new-session.sh
  new-session.sh --name SESSION --cwd DIRECTORY [--source-pane PANE]
  new-session.sh --source-session SESSION_ID

With no arguments, the script reads the session name stored by MuxPager's tmux
prompt and resolves the source directory from that pane. --source-session reads
both values from the stable session-scoped prompt handoff.
EOF
}

tmux_cmd() {
    "$TMUX_BIN" "$@"
}

notify() {
    local message="$1"
    if [ -n "$SOURCE_PANE" ]; then
        tmux_cmd display-message -d 7000 -t "$SOURCE_PANE" "MuxPager: $message" \
            >/dev/null 2>&1 || true
    elif [ -n "$SOURCE_SESSION" ]; then
        tmux_cmd display-message -d 7000 -t "$SOURCE_SESSION:" "MuxPager: $message" \
            >/dev/null 2>&1 || true
    else
        tmux_cmd display-message -d 7000 "MuxPager: $message" \
            >/dev/null 2>&1 || true
    fi
}

fail() {
    local message="$1"
    notify "$message"
    printf 'MuxPager: %s\n' "$message" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --name)
            [ "$#" -ge 2 ] || fail "--name requires a value"
            SESSION_NAME="$2"
            shift 2
            ;;
        --cwd)
            [ "$#" -ge 2 ] || fail "--cwd requires a value"
            SOURCE_CWD="$2"
            shift 2
            ;;
        --source-pane)
            [ "$#" -ge 2 ] || fail "--source-pane requires a value"
            SOURCE_PANE="$2"
            shift 2
            ;;
        --source-session)
            [ "$#" -ge 2 ] || fail "--source-session requires a value"
            SOURCE_SESSION="$2"
            SOURCE_PANE=""
            shift 2
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

# The live binding writes into session-scoped options rather than interpolating
# user input into a shell command. A session remains stable even if the sidebar
# pane that opened the prompt is rebuilt before Enter is pressed.
if [ -n "$SOURCE_SESSION" ]; then
    if [ -z "$SESSION_NAME" ]; then
        SESSION_NAME=$(tmux_cmd show-option -qv -t "$SOURCE_SESSION:" \
            "$PROMPT_OPTION" 2>/dev/null || true)
    fi
    if [ -z "$SOURCE_CWD" ]; then
        SOURCE_CWD=$(tmux_cmd show-option -qv -t "$SOURCE_SESSION:" \
            "$PROMPT_CWD_OPTION" 2>/dev/null || true)
    fi
    tmux_cmd set-option -u -t "$SOURCE_SESSION:" "$PROMPT_OPTION" \
        >/dev/null 2>&1 || true
    tmux_cmd set-option -u -t "$SOURCE_SESSION:" "$PROMPT_CWD_OPTION" \
        >/dev/null 2>&1 || true
fi

# Keep the pane-scoped handoff for compatibility with direct helper callers.
if [ -z "$SESSION_NAME" ] && [ -z "$SOURCE_SESSION" ]; then
    [ -n "$SOURCE_PANE" ] || fail "cannot identify the source tmux pane"
    SESSION_NAME=$(tmux_cmd show-option -pqv -t "$SOURCE_PANE" "$PROMPT_OPTION" 2>/dev/null || true)
    tmux_cmd set-option -pu -t "$SOURCE_PANE" "$PROMPT_OPTION" >/dev/null 2>&1 || true
fi

if [ -z "$SOURCE_CWD" ] && [ -z "$SOURCE_SESSION" ]; then
    [ -n "$SOURCE_PANE" ] || fail "cannot identify the source tmux pane"
    SOURCE_CWD=$(tmux_cmd display-message -p -t "$SOURCE_PANE" '#{pane_current_path}' 2>/dev/null || true)
fi

[ -n "$SESSION_NAME" ] || exit 0

# tmux target syntax makes ':' and '.' ambiguous, while Git ref names have a
# much larger set of edge cases. A compact portable alphabet keeps both names
# predictable and lets the session name map cleanly to its branch/worktree.
case "$SESSION_NAME" in
    [[:alnum:]]*) ;;
    *) fail "session names must start with a letter or number" ;;
esac
case "$SESSION_NAME" in
    *[![:alnum:]_-]*)
        fail "session names may contain only letters, numbers, '-' and '_'"
        ;;
esac
if [ "${#SESSION_NAME}" -gt 64 ]; then
    fail "session names must be 64 characters or fewer"
fi

if tmux_cmd has-session -t "=$SESSION_NAME" 2>/dev/null; then
    fail "session '$SESSION_NAME' already exists"
fi

[ -d "$SOURCE_CWD" ] || fail "source directory no longer exists: $SOURCE_CWD"
SOURCE_CWD=$(cd "$SOURCE_CWD" 2>/dev/null && pwd -P) || \
    fail "cannot access source directory: $SOURCE_CWD"

create_plain_session() {
    if ! tmux_cmd new-session -d -s "$SESSION_NAME" -c "$SOURCE_CWD"; then
        fail "could not create tmux session '$SESSION_NAME'"
    fi
    notify "created session '$SESSION_NAME' in $SOURCE_CWD"
    printf 'session=%s\ndirectory=%s\nworktree=false\n' "$SESSION_NAME" "$SOURCE_CWD"
}

# Git is optional for users who only want tmux session management.
if ! command -v git >/dev/null 2>&1 || \
   [ "$(git -C "$SOURCE_CWD" rev-parse --is-inside-work-tree 2>/dev/null || true)" != "true" ]; then
    create_plain_session
    exit 0
fi

GIT_COMMON_DIR=$(git -C "$SOURCE_CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
SOURCE_PREFIX=$(git -C "$SOURCE_CWD" rev-parse --show-prefix 2>/dev/null || true)
START_COMMIT=$(git -C "$SOURCE_CWD" rev-parse --verify 'HEAD^{commit}' 2>/dev/null || true)

[ -n "$GIT_COMMON_DIR" ] || fail "could not determine the Git common directory"
if [ -z "$START_COMMIT" ]; then
    fail "the repository needs an initial commit before MuxPager can create a worktree"
fi

PRIMARY_ROOT=""
while IFS= read -r worktree_line; do
    case "$worktree_line" in
        'worktree '*)
            PRIMARY_ROOT="${worktree_line#worktree }"
            break
            ;;
    esac
done < <(git -C "$SOURCE_CWD" worktree list --porcelain 2>/dev/null || true)
[ -n "$PRIMARY_ROOT" ] && [ -d "$PRIMARY_ROOT" ] || \
    fail "cannot access the primary Git checkout"
PRIMARY_ROOT=$(cd "$PRIMARY_ROOT" 2>/dev/null && pwd -P) || \
    fail "cannot resolve the primary Git checkout"
WORKTREE_BASE="$PRIMARY_ROOT/.muxpager/worktrees"

# Keep the managed worktree directory out of `git status` without touching the
# repository's tracked .gitignore.
EXCLUDE_FILE="$GIT_COMMON_DIR/info/exclude"
if ! grep -Fxq '/.muxpager/' "$EXCLUDE_FILE" 2>/dev/null; then
    mkdir -p "$(dirname "$EXCLUDE_FILE")" || fail "cannot create Git exclude directory"
    if [ -s "$EXCLUDE_FILE" ]; then
        printf '\n' >>"$EXCLUDE_FILE" || fail "cannot update $EXCLUDE_FILE"
    fi
    printf '# MuxPager-managed worktrees\n/.muxpager/\n' >>"$EXCLUDE_FILE" || \
        fail "cannot update $EXCLUDE_FILE"
fi

mkdir -p "$WORKTREE_BASE" || fail "cannot create worktree directory: $WORKTREE_BASE"

SLUG=$(printf '%s' "$SESSION_NAME" | tr '[:upper:]_' '[:lower:]-')
BRANCH_BASE="muxpager/$SLUG"
BRANCH_NAME="$BRANCH_BASE"
WORKTREE_NAME="$SLUG"
WORKTREE_PATH="$WORKTREE_BASE/$WORKTREE_NAME"
suffix=2

while git -C "$SOURCE_CWD" show-ref --verify --quiet "refs/heads/$BRANCH_NAME" 2>/dev/null || \
      [ -e "$WORKTREE_PATH" ]; do
    BRANCH_NAME="$BRANCH_BASE-$suffix"
    WORKTREE_NAME="$SLUG-$suffix"
    WORKTREE_PATH="$WORKTREE_BASE/$WORKTREE_NAME"
    suffix=$((suffix + 1))
done

worktree_error=""
if ! worktree_error=$(git -C "$SOURCE_CWD" worktree add --quiet \
        -b "$BRANCH_NAME" "$WORKTREE_PATH" "$START_COMMIT" 2>&1); then
    [ -n "$worktree_error" ] || worktree_error="unknown Git error"
    fail "could not create worktree: $worktree_error"
fi

SESSION_CWD="$WORKTREE_PATH"
SOURCE_PREFIX="${SOURCE_PREFIX%/}"
if [ -n "$SOURCE_PREFIX" ]; then
    SESSION_CWD="$WORKTREE_PATH/$SOURCE_PREFIX"
    if ! mkdir -p "$SESSION_CWD"; then
        git -C "$SOURCE_CWD" worktree remove --force "$WORKTREE_PATH" >/dev/null 2>&1 || true
        git -C "$SOURCE_CWD" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
        fail "could not create matching subdirectory in the worktree"
    fi
fi

if ! tmux_cmd new-session -d -s "$SESSION_NAME" -c "$SESSION_CWD"; then
    # The worktree and branch were created by this invocation and have never
    # hosted a session, so rolling them back cannot discard user work.
    git -C "$SOURCE_CWD" worktree remove --force "$WORKTREE_PATH" >/dev/null 2>&1 || true
    git -C "$SOURCE_CWD" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
    fail "could not create tmux session '$SESSION_NAME'"
fi

# Session metadata is informational today and gives future cleanup/status tools
# a reliable identity without having to infer it from directory names.
tmux_cmd set-option -t "$SESSION_NAME:" @muxpager-worktree-path "$WORKTREE_PATH" >/dev/null 2>&1 || true
tmux_cmd set-option -t "$SESSION_NAME:" @muxpager-worktree-branch "$BRANCH_NAME" >/dev/null 2>&1 || true
tmux_cmd set-option -t "$SESSION_NAME:" @muxpager-worktree-source "$PRIMARY_ROOT" >/dev/null 2>&1 || true

notify "created '$SESSION_NAME' on $BRANCH_NAME in $WORKTREE_PATH"
printf 'session=%s\ndirectory=%s\nworktree=true\nbranch=%s\nworktree_path=%s\n' \
    "$SESSION_NAME" "$SESSION_CWD" "$BRANCH_NAME" "$WORKTREE_PATH"
