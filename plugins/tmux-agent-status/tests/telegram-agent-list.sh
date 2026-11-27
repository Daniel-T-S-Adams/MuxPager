#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_HOME="$TMP_DIR/home"
FAKE_BIN="$TMP_DIR/bin"
STATUS_DIR="$TEST_HOME/.cache/tmux-agent-status"
PROC_ROOT="$TMP_DIR/proc"
mkdir -p "$FAKE_BIN" "$STATUS_DIR/panes" "$PROC_ROOT/101"

cat > "$FAKE_BIN/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "list-panes" ] && [ "${2:-}" = "-a" ]; then
    printf '%%1\tagents\t0\tcodex\t0\t100\t0\twork\n'
    printf '%%2\tshell\t0\tbash\t0\t200\t0\tshell\n'
    exit 0
fi
exit 1
EOF
chmod +x "$FAKE_BIN/tmux"

cat > "$FAKE_BIN/ps" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "$*" = "-eo pid=,ppid=,stat=,args=" ]; then
    cat <<'OUT'
100 1 S -bash
101 100 S /usr/local/bin/codex
200 1 S -bash
201 200 S sleep infinity
OUT
    exit 0
fi
exit 1
EOF
chmod +x "$FAKE_BIN/ps"

echo "done" > "$STATUS_DIR/panes/agents_%1.status"
printf 'codex\ncodex-session-123\n' > "$STATUS_DIR/panes/agents_%1.conversation"

# A fake procfs: the machine booted at BOOT_TIME and the agent (pid 101)
# started START_OFFSET seconds later. Field 22 of /proc/<pid>/stat is the start
# offset in clock ticks; the command name deliberately contains a space and a
# parenthesis to exercise the parser.
BOOT_TIME=1700000000
START_OFFSET=5000
PROCESS_STARTED=$((BOOT_TIME + START_OFFSET))
TICKS_PER_SECOND=$(getconf CLK_TCK 2>/dev/null || echo 100)
printf 'cpu  1 2 3 4\nintr 9 9 9\nbtime %s\nprocesses 7\n' "$BOOT_TIME" > "$PROC_ROOT/stat"
printf '101 (co) dex) S 100 101 100 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 %s 0 0\n' \
    "$((START_OFFSET * TICKS_PER_SECOND))" > "$PROC_ROOT/101/stat"

list_agents() {
    PATH="$FAKE_BIN:$PATH" HOME="$TEST_HOME" \
        MUXPAGER_PROC_ROOT="$PROC_ROOT" bash "$REPO_DIR/scripts/list-agent-panes.sh"
}

set_identity_time() {
    touch -d "@$1" "$STATUS_DIR/panes/agents_%1.conversation"
}

with_identity=$'%1\tagents\t0\tcodex\t0\tcodex\tdone\tcodex-session-123'
without_identity=$'%1\tagents\t0\tcodex\t0\tcodex\tdone\t'

assert_listing() {
    local expected="$1"
    local message="$2"
    local actual
    actual=$(list_agents)
    if [ "$actual" != "$expected" ]; then
        echo "Assertion failed: $message" >&2
        echo "Expected: $expected" >&2
        echo "Actual:   $actual" >&2
        exit 1
    fi
}

set_identity_time "$((PROCESS_STARTED + 60))"
assert_listing "$with_identity" \
    "Telegram bridge agent listing should contain only the live agent pane"

set_identity_time "$PROCESS_STARTED"
assert_listing "$with_identity" \
    "an identity written in the same second the agent started should be kept"

set_identity_time "$((PROCESS_STARTED - 60))"
assert_listing "$without_identity" \
    "an identity older than the agent process should be suppressed"

# Regression: the mtime of /proc/<pid> is when the kernel created that procfs
# inode, not when the process started. Inside a sandboxed systemd service it is
# far newer than the identity of a long-running agent, and trusting it closed
# the Telegram topic of a live conversation.
set_identity_time "$((PROCESS_STARTED + 60))"
touch "$PROC_ROOT/101"
assert_listing "$with_identity" \
    "a fresh /proc/<pid> directory mtime must not hide a live conversation identity"

# Without a readable start time the identity is kept rather than guessed away.
rm "$PROC_ROOT/101/stat"
set_identity_time "$((PROCESS_STARTED - 60))"
assert_listing "$with_identity" \
    "an unknown process start time must not suppress the identity"

echo "Telegram agent pane listing checks passed"
