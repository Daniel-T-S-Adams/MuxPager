#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_HOME="$TMP_DIR/home"
FAKE_BIN="$TMP_DIR/bin"
STATUS_DIR="$TEST_HOME/.cache/tmux-agent-status"
PANE_DIR="$STATUS_DIR/panes"

mkdir -p "$FAKE_BIN" "$PANE_DIR"

tab=$'\t'
cat > "$FAKE_BIN/tmux" <<EOF
#!/usr/bin/env bash
set -euo pipefail

case "\${1:-}" in
    list-sessions)
        echo main
        echo stale-shell
        ;;
    list-panes)
        if [ "\${2:-}" = "-a" ]; then
            printf 'main${tab}%%1${tab}/repo${tab}100${tab}0${tab}bash${tab}bash${tab}\n'
            printf 'main${tab}%%2${tab}/repo${tab}200${tab}1${tab}codex${tab}codex${tab}\n'
            printf 'main${tab}%%9${tab}/repo${tab}900${tab}1${tab}codex${tab}bash${tab}agent-sidebar\n'
            printf 'stale-shell${tab}%%3${tab}/repo${tab}300${tab}0${tab}bash${tab}bash${tab}\n'
            exit 0
        fi
        exit 1
        ;;
    show-option)
        echo off
        ;;
    *)
        exit 1
        ;;
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
101 100 T /usr/local/bin/codex
102 101 S /opt/codex/codex-code-mode-host
200 1 S -bash
201 200 S /usr/local/bin/codex
202 201 D long-running-build
300 1 S -bash
900 1 S bash sidebar.sh
OUT
EOF
chmod +x "$FAKE_BIN/ps"

printf 'done' > "$STATUS_DIR/main.status"
printf 'codex' > "$PANE_DIR/main_%1.agent"
printf 'done' > "$PANE_DIR/main_%1.status"
printf 'codex' > "$PANE_DIR/main_%2.agent"
printf 'done' > "$PANE_DIR/main_%2.status"

printf 'done' > "$STATUS_DIR/stale-shell.status"
printf 'claude' > "$PANE_DIR/stale-shell_%3.agent"
printf 'done' > "$PANE_DIR/stale-shell_%3.status"

# Put hook markers beyond the startup grace so process liveness is authoritative.
touch -t 202001010000 "$PANE_DIR/main_%1.agent" "$PANE_DIR/main_%1.status"
touch -t 202001010000 "$PANE_DIR/main_%2.agent" "$PANE_DIR/main_%2.status"
touch -t 202001010000 "$PANE_DIR/stale-shell_%3.agent" "$PANE_DIR/stale-shell_%3.status"

PATH="$FAKE_BIN:$PATH" HOME="$TEST_HOME" \
    "$REPO_DIR/scripts/sidebar-collector.sh" --once >/dev/null

CACHE_FILE="$STATUS_DIR/.sidebar-cache"

assert_contains() {
    local needle="$1"
    local message="$2"
    if ! grep -Fq "$needle" "$CACHE_FILE"; then
        echo "Assertion failed: $message" >&2
        sed -n '1,160p' "$CACHE_FILE" >&2
        exit 1
    fi
}

assert_not_exists() {
    local path="$1"
    local message="$2"
    if [ -e "$path" ]; then
        echo "Assertion failed: $message" >&2
        exit 1
    fi
}

assert_contains $'R:S|main|done||\tmain\tS' \
    "the live idle Codex should keep the session waiting on the user"
assert_contains $'R:P|main|%1|bash|noagent|0\tmain:w0\tP' \
    "the window containing only a stopped Codex should become a green shell row"
assert_contains $'R:P|main|%2|codex|done|1\tmain:w1\tP' \
    "the usable Codex should remain an agent row while its child is in D state"
assert_contains $'R:S|stale-shell|noagent||\tstale-shell\tS' \
    "exited Claude metadata should not turn a shell-only session into an agent"

assert_not_exists "$PANE_DIR/main_%1.agent" "stopped Codex identity should be pruned"
assert_not_exists "$PANE_DIR/main_%1.status" "stopped Codex status should be pruned"
assert_not_exists "$PANE_DIR/stale-shell_%3.agent" "exited Claude identity should be pruned"
assert_not_exists "$PANE_DIR/stale-shell_%3.status" "exited Claude status should be pruned"

if [ ! -f "$PANE_DIR/main_%2.agent" ]; then
    echo "Assertion failed: usable Codex metadata should remain" >&2
    exit 1
fi

echo "sidebar stopped-agent classification regression checks passed"
