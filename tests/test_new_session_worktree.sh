#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
TMUX_SOCKET="$TEST_ROOT/tmux.sock"

cleanup() {
    tmux -S "$TMUX_SOCKET" kill-server 2>/dev/null || true
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

tmux_test() {
    tmux -S "$TMUX_SOCKET" "$@"
}

run_new_session() {
    TMUX="$TMUX_SOCKET,0,0" TMUX_PANE= \
        "$PROJECT_ROOT/scripts/new-session.sh" "$@"
}

assert_eq() {
    local actual="$1" expected="$2" message="$3"
    if [ "$actual" != "$expected" ]; then
        printf 'Assertion failed: %s\nExpected: %s\nActual:   %s\n' \
            "$message" "$expected" "$actual" >&2
        exit 1
    fi
}

# tmux reads a pane's path from its child process. That process has not always
# changed into its start directory by the time the session-creating command
# returns, so wait for the path to settle instead of sampling it once.
assert_pane_path() {
    local session="$1" expected="$2" message="$3" actual="" _attempt
    for _attempt in $(seq 1 50); do
        actual="$(tmux_test list-panes -t "=$session" \
            -F '#{pane_current_path}' 2>/dev/null || true)"
        [ "$actual" = "$expected" ] && return 0
        sleep 0.1
    done
    assert_eq "$actual" "$expected" "$message"
}

REPO="$TEST_ROOT/repo with spaces"
mkdir -p "$REPO/src/api"
git -C "$REPO" init -q
git -C "$REPO" config user.name MuxPager-Test
git -C "$REPO" config user.email muxpager@example.invalid
touch "$REPO/src/api/tracked.txt"
git -C "$REPO" add src/api/tracked.txt
git -C "$REPO" commit -qm initial

tmux_test -f /dev/null new-session -d -s origin -c "$REPO/src/api"
SOURCE_PANE="$(tmux_test list-panes -t origin -F '#{pane_id}')"

# Exercise the helper's backward-compatible pane-scoped handoff. The mixed-case
# tmux name is preserved while its Git branch/path receive a portable slug.
tmux_test set-option -p -t "$SOURCE_PANE" \
    @muxpager-new-session-name Feature_One
tmux_test run-shell -b -t "$SOURCE_PANE" \
    "\"$PROJECT_ROOT/scripts/new-session.sh\" --source-pane \"$SOURCE_PANE\""
for _attempt in $(seq 1 50); do
    tmux_test has-session -t '=Feature_One' 2>/dev/null && break
    sleep 0.1
done
if ! tmux_test has-session -t '=Feature_One' 2>/dev/null; then
    echo "Assertion failed: the tmux run-shell binding did not create the session" >&2
    exit 1
fi

EXPECTED_WORKTREE="$REPO/.muxpager/worktrees/feature-one"
EXPECTED_CWD="$EXPECTED_WORKTREE/src/api"
assert_pane_path Feature_One \
    "$EXPECTED_CWD" \
    "the new session should preserve the source subdirectory inside its worktree"
assert_eq \
    "$(git -C "$EXPECTED_WORKTREE" branch --show-current)" \
    "muxpager/feature-one" \
    "the worktree should use a session-specific branch"
assert_eq \
    "$(tmux_test show-option -pqv -t "$SOURCE_PANE" @muxpager-new-session-name)" \
    "" \
    "the prompt handoff option should be consumed"
assert_eq \
    "$(git -C "$REPO" status --short)" \
    "" \
    "the managed worktree directory should not dirty the primary checkout"
assert_eq \
    "$(grep -Fxc '/.muxpager/' "$(git -C "$REPO" rev-parse --path-format=absolute --git-path info/exclude)")" \
    "1" \
    "the local exclude should contain one MuxPager entry"
assert_eq \
    "$(tmux_test show-option -qv -t 'Feature_One:' @muxpager-worktree-branch)" \
    "muxpager/feature-one" \
    "the tmux session should record its worktree branch"

# The interactive prompt may be opened from a sidebar pane which is rebuilt
# before Enter is pressed. Its session-scoped handoff must remain usable after
# that exact source pane has disappeared.
tmux_test new-session -d -s prompt-race -c "$REPO/src/api"
PROMPT_SESSION="$(tmux_test display-message -p -t '=prompt-race:' '#{session_id}')"
STALE_PANE="$(tmux_test list-panes -t '=prompt-race' -F '#{pane_id}')"
tmux_test split-window -d -t "$STALE_PANE" -c "$REPO"
tmux_test set-option -t "$PROMPT_SESSION:" \
    @muxpager-new-session-name Session_Handoff
tmux_test set-option -t "$PROMPT_SESSION:" \
    @muxpager-new-session-cwd "$REPO/src/api"
tmux_test kill-pane -t "$STALE_PANE"
run_new_session --source-session "$PROMPT_SESSION" >/dev/null
assert_pane_path Session_Handoff \
    "$REPO/.muxpager/worktrees/session-handoff/src/api" \
    "session handoff should survive deletion of the pane that opened the prompt"
assert_eq \
    "$(tmux_test show-option -qv -t "$PROMPT_SESSION:" @muxpager-new-session-name)" \
    "" \
    "the session-scoped name handoff should be consumed"
assert_eq \
    "$(tmux_test show-option -qv -t "$PROMPT_SESSION:" @muxpager-new-session-cwd)" \
    "" \
    "the session-scoped cwd handoff should be consumed"

# Creating from an existing linked worktree still places the new worktree under
# the primary checkout rather than nesting managed directories recursively.
run_new_session --name from-worktree --cwd "$EXPECTED_CWD" >/dev/null
assert_pane_path from-worktree \
    "$REPO/.muxpager/worktrees/from-worktree/src/api" \
    "sessions created from worktrees should reuse the primary checkout's managed root"

# Existing branches are never reused or overwritten.
git -C "$REPO" branch muxpager/collision
run_new_session --name collision --cwd "$REPO" >/dev/null
assert_eq \
    "$(git -C "$REPO/.muxpager/worktrees/collision-2" branch --show-current)" \
    "muxpager/collision-2" \
    "branch collisions should receive a numeric suffix"

# Non-Git directories retain normal session behavior and their exact cwd.
PLAIN_DIR="$TEST_ROOT/plain directory"
mkdir -p "$PLAIN_DIR"
plain_output="$(run_new_session --name plain-session --cwd "$PLAIN_DIR")"
assert_pane_path plain-session \
    "$PLAIN_DIR" \
    "non-Git sessions should start in the requested directory"
case "$plain_output" in
    *'worktree=false'*) ;;
    *) echo "Assertion failed: non-Git result should report worktree=false" >&2; exit 1 ;;
esac

if run_new_session --name 'bad:name' --cwd "$REPO" >/dev/null 2>&1; then
    echo "Assertion failed: ambiguous tmux/Git session names should be rejected" >&2
    exit 1
fi
if run_new_session --name Feature_One --cwd "$REPO" >/dev/null 2>&1; then
    echo "Assertion failed: duplicate tmux session names should be rejected" >&2
    exit 1
fi

UNBORN_REPO="$TEST_ROOT/unborn"
git -C "$TEST_ROOT" init -q "$UNBORN_REPO"
if run_new_session --name unborn-session --cwd "$UNBORN_REPO" >/dev/null 2>&1; then
    echo "Assertion failed: an unborn repository cannot seed a worktree" >&2
    exit 1
fi
if tmux_test has-session -t '=unborn-session' 2>/dev/null; then
    echo "Assertion failed: worktree failure must not create a shared-cwd session" >&2
    exit 1
fi

echo "worktree-aware session creation checks passed"
