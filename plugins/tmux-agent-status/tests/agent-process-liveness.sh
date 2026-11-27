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

assert_agent_name() {
    local args="$1" expected="$2" actual
    actual=$(agent_name_from_args "$args" || true)
    if [ "$actual" != "$expected" ]; then
        echo "Assertion failed: '$args' should be detected as '${expected:-no agent}', got '${actual:-no agent}'" >&2
        exit 1
    fi
}

# Claude Code's native installer runs each release from
# .../claude/versions/<version>. A process started through that resolved path
# (rather than the `claude` symlink) is still Claude.
assert_agent_name "/root/.local/bin/claude" "claude"
assert_agent_name "/root/.local/share/claude/versions/2.1.332" "claude"
assert_agent_name "/home/dev/.local/share/claude/versions/2.1.332 --resume abc" "claude"
# Only the release binary itself counts: not a file below it, not a path merely
# passed as an argument, and not another tool with a versions directory.
assert_agent_name "/opt/claude/versions/2.1.332/helper" ""
assert_agent_name "cat /root/.local/share/claude/versions/2.1.332" ""
assert_agent_name "/opt/codex/versions/1.0" ""
# The rule only adds a match. An agent binary nested deeper under such a
# directory is still found by its own name.
assert_agent_name "/opt/claude/versions/1.0/bin/claude" "claude"
assert_agent_name "/opt/claude/versions/1.0/bin/codex" "codex"

echo "agent process liveness checks passed"
