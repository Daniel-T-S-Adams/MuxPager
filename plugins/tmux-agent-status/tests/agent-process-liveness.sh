#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAKE_BIN="$TMP_DIR/bin"
mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

[ "${1:-}" = "list-panes" ] || exit 1
case "${3:-}" in
    codex-waiting)  echo 100 ;;
    claude-waiting) echo 200 ;;
    codex-stopped)  echo 300 ;;
    claude-zombie)  echo 400 ;;
    not-an-agent)   echo 500 ;;
    *) exit 1 ;;
esac
EOF
chmod +x "$FAKE_BIN/tmux"

cat > "$FAKE_BIN/ps" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "$*" != "-eo pid=,ppid=,stat=,args=" ]; then
    exit 1
fi

cat <<'OUT'
100 1 S -bash
101 100 S /usr/local/bin/codex
102 101 D long-running-compiler
200 1 S -bash
201 200 S node /opt/claude/bin/claude
202 201 R long-running-test-suite
300 1 S -bash
301 300 T /usr/local/bin/codex
302 301 S /opt/codex/codex-code-mode-host
400 1 S -bash
401 400 Z /usr/local/bin/claude
500 1 S -bash
501 500 R rg codex
OUT
EOF
chmod +x "$FAKE_BIN/ps"

export PATH="$FAKE_BIN:$PATH"

# shellcheck source=../scripts/lib/agent-processes.sh
source "$REPO_DIR/scripts/lib/agent-processes.sh"

assert_detected() {
    local session="$1"
    if ! session_has_agent_process "$session"; then
        echo "Assertion failed: $session should contain a usable agent" >&2
        exit 1
    fi
}

assert_not_detected() {
    local session="$1"
    if session_has_agent_process "$session"; then
        echo "Assertion failed: $session should not contain a usable agent" >&2
        exit 1
    fi
}

# Sleeping agents remain valid while a child is running or blocked in I/O.
assert_detected codex-waiting
assert_detected claude-waiting

# Terminal process states never make a pane an agent pane. The live Codex
# helper below the stopped root must not reactivate it.
assert_not_detected codex-stopped
assert_not_detected claude-zombie
assert_not_detected not-an-agent

for live_state in R S D I; do
    agent_process_state_is_usable "$live_state" || {
        echo "Assertion failed: live state $live_state should remain usable" >&2
        exit 1
    }
done

for terminal_state in T t Z X x; do
    if agent_process_state_is_usable "$terminal_state"; then
        echo "Assertion failed: terminal state $terminal_state should be rejected" >&2
        exit 1
    fi
done

echo "agent process liveness checks passed"
