#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"

cleanup() {
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

TEST_HOME="$TEST_ROOT/home"
FAKE_BIN="$TEST_ROOT/bin"
mkdir -p "$TEST_HOME" "$FAKE_BIN"

for command_path in \
    /usr/bin/bash \
    /usr/bin/dirname \
    /usr/bin/python3 \
    /usr/bin/tmux \
    /usr/bin/ps \
    /usr/bin/pgrep; do
    ln -s "$command_path" "$FAKE_BIN/$(basename "$command_path")"
done

set +e
OUTPUT="$(
    HOME="$TEST_HOME" \
    PATH="$FAKE_BIN" \
    TMUX_AGENT_SYSTEMCTL_BIN=/bin/true \
    TMUX_AGENT_SKIP_LIVE_RELOAD=1 \
        /usr/bin/bash "$PROJECT_ROOT/install.sh" 2>&1
)"
STATUS=$?
set -e

if [ "$STATUS" -eq 0 ]; then
    echo "installer unexpectedly succeeded without fzf" >&2
    exit 1
fi

case "$OUTPUT" in
    *"Required command not found: fzf"*) ;;
    *)
        printf 'unexpected installer error:\n%s\n' "$OUTPUT" >&2
        exit 1
        ;;
esac

test ! -e "$TEST_HOME/.tmux.conf"
test ! -e "$TEST_HOME/.codex"
test ! -e "$TEST_HOME/.claude"
test ! -e "$TEST_HOME/.config"

echo "installer dependency preflight checks passed"
