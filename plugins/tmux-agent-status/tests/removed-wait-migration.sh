#!/usr/bin/env bash

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d)"

cleanup() {
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

export HOME="$TEST_ROOT/home"
STATUS_DIR="$HOME/.cache/tmux-agent-status"
mkdir -p "$STATUS_DIR/wait" "$STATUS_DIR/panes"

echo "wait" > "$STATUS_DIR/legacy.status"
echo "wait" > "$STATUS_DIR/remote-remote.status"
echo "wait" > "$STATUS_DIR/panes/legacy_%1.status"
echo "123" > "$STATUS_DIR/wait/legacy.wait"
echo "123" > "$STATUS_DIR/wait/legacy_%1.wait"

# shellcheck source=../scripts/lib/session-status.sh
source "$TEST_DIR/../scripts/lib/session-status.sh"

for status_file in \
    "$STATUS_DIR/legacy.status" \
    "$STATUS_DIR/remote-remote.status" \
    "$STATUS_DIR/panes/legacy_%1.status"; do
    if [ "$(cat "$status_file")" != "done" ]; then
        echo "Assertion failed: legacy wait status should migrate to done: $status_file" >&2
        exit 1
    fi
done

if [ -e "$STATUS_DIR/wait" ]; then
    echo "Assertion failed: legacy wait marker directory should be removed" >&2
    exit 1
fi

PLUGIN_FILE="$TEST_DIR/../tmux-agent-status.tmux"
if grep -Eq '^[[:space:]]*tmux bind-key .*wait-session\\.sh' "$PLUGIN_FILE"; then
    echo "Assertion failed: the removed wait action must not be rebound" >&2
    exit 1
fi
if ! grep -Fq 'tmux unbind-key -T prefix "$legacy_wait_key"' "$PLUGIN_FILE"; then
    echo "Assertion failed: upgrades should remove the old wait key binding" >&2
    exit 1
fi

echo "removed wait-state migration checks passed"
