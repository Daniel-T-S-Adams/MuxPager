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

init_repo() {
    local repository="$1"
    mkdir -p "$repository"
    git -C "$repository" init -q
    git -C "$repository" config user.name MuxPager-Test
    git -C "$repository" config user.email muxpager@example.invalid
    git -C "$repository" commit --allow-empty -qm initial
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

WORKSPACE="$TEST_ROOT/workspace with spaces"
SOURCE_REPO="$WORKSPACE/MuxPager"
TARGET_REPO="$WORKSPACE/tig-swarm-demo"
NESTED_REPO="$TARGET_REPO/tig-monorepo"
init_repo "$SOURCE_REPO"
init_repo "$TARGET_REPO"
init_repo "$NESTED_REPO"

repository_list="$(
    MUXPAGER_ROOT="$SOURCE_REPO" \
        "$PROJECT_ROOT/scripts/new-session-picker.sh" \
        --cwd "$SOURCE_REPO" --workspace-root "$WORKSPACE" --list
)"
for expected_repo in "$SOURCE_REPO" "$TARGET_REPO" "$NESTED_REPO"; do
    if ! grep -Fxq "$expected_repo" <<<"$repository_list"; then
        echo "repository picker did not discover $expected_repo" >&2
        exit 1
    fi
done

tmux_test -f /dev/null new-session -d -s origin -c "$SOURCE_REPO"
TMUX="$TMUX_SOCKET,0,0" TMUX_PANE= \
    "$PROJECT_ROOT/scripts/new-session-picker.sh" \
    --cwd "$SOURCE_REPO" \
    --workspace-root "$WORKSPACE" \
    --repo "$TARGET_REPO" \
    --name Swarm_Feature >/dev/null

TARGET_WORKTREE="$TARGET_REPO/.muxpager/worktrees/swarm-feature"
assert_eq \
    "$(git -C "$TARGET_WORKTREE" branch --show-current)" \
    "muxpager/swarm-feature" \
    "an explicitly selected repository should own the new branch"
assert_pane_path Swarm_Feature \
    "$TARGET_WORKTREE" \
    "the selected repository worktree should be the new session directory"
if [ -e "$SOURCE_REPO/.muxpager" ]; then
    echo "repository selection must not create a worktree in the starting repository" >&2
    exit 1
fi

# Exercise the interactive selection path with a deterministic fzf stand-in.
FAKE_BIN="$TEST_ROOT/bin"
mkdir -p "$FAKE_BIN"
cat >"$FAKE_BIN/fzf" <<'EOF'
#!/usr/bin/env bash
grep -Fx "$MUXPAGER_TEST_REPOSITORY"
EOF
chmod +x "$FAKE_BIN/fzf"

printf 'Picker_Feature\n' | \
    PATH="$FAKE_BIN:$PATH" \
    MUXPAGER_TEST_REPOSITORY="$TARGET_REPO" \
    MUXPAGER_ROOT="$SOURCE_REPO" \
    TMUX="$TMUX_SOCKET,0,0" \
    TMUX_PANE= \
        "$PROJECT_ROOT/scripts/new-session-picker.sh" \
        --cwd "$SOURCE_REPO" --workspace-root "$WORKSPACE" >/dev/null

assert_eq \
    "$(git -C "$TARGET_REPO/.muxpager/worktrees/picker-feature" branch --show-current)" \
    "muxpager/picker-feature" \
    "the fzf-selected repository should own the interactive worktree"
assert_eq \
    "$(printf '%s\n' "$repository_list" | grep -Fxc "$TARGET_REPO")" \
    "1" \
    "repository discovery should list each primary checkout once"

echo "repository-selecting session creation checks passed"
