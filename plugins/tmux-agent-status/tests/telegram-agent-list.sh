#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_HOME="$TMP_DIR/home"
FAKE_BIN="$TMP_DIR/bin"
STATUS_DIR="$TEST_HOME/.cache/tmux-agent-status"
mkdir -p "$FAKE_BIN" "$STATUS_DIR/panes"

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

actual=$(PATH="$FAKE_BIN:$PATH" HOME="$TEST_HOME" bash "$REPO_DIR/scripts/list-agent-panes.sh")
expected=$'%1\tagents\t0\tcodex\t0\tcodex\tdone\tcodex-session-123'
if [ "$actual" != "$expected" ]; then
    echo "Assertion failed: Telegram bridge agent listing should contain only the live agent pane" >&2
    echo "Expected: $expected" >&2
    echo "Actual:   $actual" >&2
    exit 1
fi

echo "Telegram agent pane listing checks passed"
