#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_HOME="$TMP_DIR/home"
FAKE_BIN="$TMP_DIR/bin"
STATUS_DIR="$TEST_HOME/.cache/tmux-agent-status"
PANE_DIR="$STATUS_DIR/panes"
STATE_DIR="$TMP_DIR/state"

mkdir -p "$FAKE_BIN" "$PANE_DIR" "$STATE_DIR"

tab=$'\t'
cat > "$FAKE_BIN/tmux" <<EOF
#!/usr/bin/env bash
set -euo pipefail

case "\${1:-}" in
    list-panes)
        if [ "\${2:-}" = "-a" ]; then
            printf 'working-agent${tab}%%1${tab}0${tab}main${tab}bash${tab}\n'
            printf 'idle-agent${tab}%%2${tab}0${tab}main${tab}bash${tab}\n'
            printf 'asking-agent${tab}%%3${tab}0${tab}main${tab}bash${tab}\n'
            printf 'shell-only${tab}%%4${tab}0${tab}main${tab}bash${tab}\n'
            printf 'mixed${tab}%%5${tab}0${tab}bash${tab}bash${tab}\n'
            printf 'mixed${tab}%%6${tab}1${tab}codex${tab}codex${tab}\n'
            exit 0
        fi
        exit 1
        ;;
    *)
        exit 1
        ;;
esac
EOF
chmod +x "$FAKE_BIN/tmux"

printf 'working' > "$PANE_DIR/working-agent_%1.status"
printf 'codex' > "$PANE_DIR/working-agent_%1.agent"
printf 'done' > "$PANE_DIR/idle-agent_%2.status"
printf 'claude' > "$PANE_DIR/idle-agent_%2.agent"
printf 'ask' > "$PANE_DIR/asking-agent_%3.status"
printf 'codex' > "$PANE_DIR/asking-agent_%3.agent"
printf 'done' > "$STATUS_DIR/mixed.status"
printf 'done' > "$PANE_DIR/mixed_%6.status"
printf 'codex' > "$PANE_DIR/mixed_%6.agent"

switcher_output="$(PATH="$FAKE_BIN:$PATH" HOME="$TEST_HOME" \
    "$REPO_DIR/scripts/hook-based-switcher.sh" --list)"

assert_contains() {
    local needle="$1"
    local message="$2"

    if ! printf '%s\n' "$switcher_output" | grep -Fq "$needle"; then
        echo "Assertion failed: $message" >&2
        printf '%s\n' "$switcher_output" >&2
        exit 1
    fi
}

assert_contains $'\033[1;33m⣾\033[0m  ▸ [session] working-agent' \
    "working agents should use the yellow thinking-dots icon"
assert_contains $'\033[1;31m?\033[0m  ▸ [session] idle-agent' \
    "finished agents should use the red waiting-on-you icon"
assert_contains $'\033[1;31m?\033[0m  ▸ [session] asking-agent' \
    "agents asking a question should use the red waiting-on-you icon"
assert_contains $'\033[1;32m✓\033[0m  ▸ [session] shell-only' \
    "targets without a tracked agent should use the green tick"

PATH="$FAKE_BIN:$PATH" HOME="$TEST_HOME" \
    "$REPO_DIR/scripts/hook-based-switcher.sh" --state-dir "$STATE_DIR" \
    --toggle-expand "mixed" "S"
mixed_output="$(PATH="$FAKE_BIN:$PATH" HOME="$TEST_HOME" \
    "$REPO_DIR/scripts/hook-based-switcher.sh" --state-dir "$STATE_DIR" --list)"

if ! printf '%s\n' "$mixed_output" | grep -Fq \
    $'\033[1;32m✓\033[0m    ▸ [window] mixed / bash'; then
    echo "Assertion failed: a shell window must not inherit its sibling agent's status" >&2
    printf '%s\n' "$mixed_output" >&2
    exit 1
fi
if ! printf '%s\n' "$mixed_output" | grep -Fq \
    $'\033[1;31m?\033[0m    ▸ [window] mixed / codex'; then
    echo "Assertion failed: the tracked sibling window should remain waiting on the user" >&2
    printf '%s\n' "$mixed_output" >&2
    exit 1
fi

echo "switcher icon semantics regression checks passed"
