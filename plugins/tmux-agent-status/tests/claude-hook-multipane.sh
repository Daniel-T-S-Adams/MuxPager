#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_HOME="$TMP_DIR/home"
FAKE_BIN="$TMP_DIR/bin"
STATUS_DIR="$TEST_HOME/.cache/tmux-agent-status"
PARKED_DIR="$STATUS_DIR/parked"
PANE_DIR="$STATUS_DIR/panes"
REFRESH_FILE="$STATUS_DIR/.sidebar-refresh"

mkdir -p "$FAKE_BIN" "$STATUS_DIR" "$PARKED_DIR" "$PANE_DIR"

cat > "$FAKE_BIN/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
    display-message)
        if [ "${2:-}" = "-p" ] && [ "${3:-}" = "#{session_name}" ]; then
            echo "mixed-hooks"
            exit 0
        fi
        ;;
esac

exit 1
EOF
chmod +x "$FAKE_BIN/tmux"

assert_eq() {
    local expected="$1"
    local actual="$2"
    local message="$3"

    if [ "$expected" != "$actual" ]; then
        echo "Assertion failed: $message" >&2
        echo "Expected: $expected" >&2
        echo "Actual:   $actual" >&2
        exit 1
    fi
}

run_hook() {
    local hook_name="$1"
    local pane_id="$2"
    local payload=""
    if [ "$#" -ge 3 ]; then
        payload="$3"
    else
        printf -v payload '{"hook_event_name":"%s"}' "$hook_name"
    fi

    printf '%s\n' "$payload" | \
        PATH="$FAKE_BIN:$PATH" \
        HOME="$TEST_HOME" \
        TMUX="/tmp/tmux-test,4242,0" \
        TMUX_PANE="$pane_id" \
        "$REPO_DIR/hooks/better-hook.sh" "$hook_name"
}

run_hook "SessionStart" "%3" \
    '{"hook_event_name":"SessionStart","session_id":"claude-session-456","source":"startup"}'
conversation_agent="$(sed -n '1p' "$PANE_DIR/mixed-hooks_%3.conversation")"
conversation_id="$(sed -n '2p' "$PANE_DIR/mixed-hooks_%3.conversation")"
assert_eq "claude" "$conversation_agent" "Claude hooks should identify the conversation agent"
assert_eq "claude-session-456" "$conversation_id" "Claude hooks should persist the stable session ID"

echo "parked" > "$PANE_DIR/mixed-hooks_%3.status"
: > "$PARKED_DIR/mixed-hooks_%3.parked"
run_hook "UserPromptSubmit" "%3"
session_status="$(cat "$STATUS_DIR/mixed-hooks.status")"
claude_status="$(cat "$PANE_DIR/mixed-hooks_%3.status")"
assert_eq "working" "$session_status" "Claude UserPromptSubmit should mark the session working"
assert_eq "working" "$claude_status" "Claude UserPromptSubmit should mark the current pane working"
[ -f "$REFRESH_FILE" ] || { echo "Assertion failed: Claude UserPromptSubmit should touch sidebar refresh marker" >&2; exit 1; }
prompt_refresh=$(<"$REFRESH_FILE")
if [ -f "$PARKED_DIR/mixed-hooks_%3.parked" ]; then
    echo "Assertion failed: Claude UserPromptSubmit should clear the current pane parked override" >&2
    exit 1
fi

run_hook "UserPromptSubmit" "%3" \
    '{"hook_event_name":"UserPromptSubmit","session_id":"claude-session-456","prompt":"Exact local Claude prompt.\nSecond line."}'
prompt_file=$(find "$PANE_DIR/conversations" -type f -name '*.prompt' -print -quit)
[ -n "$prompt_file" ] || {
    echo "Assertion failed: Claude UserPromptSubmit should queue the exact local prompt" >&2
    exit 1
}
assert_eq $'Exact local Claude prompt.\nSecond line.' "$(cat "$prompt_file")" \
    "Claude UserPromptSubmit should preserve the complete prompt"

echo "working" > "$PANE_DIR/mixed-hooks_%13.status"
echo "codex" > "$PANE_DIR/mixed-hooks_%13.agent"

run_hook "Stop" "%3" '{"hook_event_name":"Stop","session_id":"claude-session-456","last_assistant_message":"Exact Claude response."}'
stop_refresh=$(<"$REFRESH_FILE")
if [ "$stop_refresh" = "$prompt_refresh" ]; then
    echo "Assertion failed: consecutive Claude hooks should write distinct refresh tokens" >&2
    exit 1
fi
session_status="$(cat "$STATUS_DIR/mixed-hooks.status")"
claude_status="$(cat "$PANE_DIR/mixed-hooks_%3.status")"
claude_agent="$(cat "$PANE_DIR/mixed-hooks_%3.agent")"
assert_eq "working" "$session_status" "Claude finishing in one pane must not mark the whole mixed session done"
assert_eq "done" "$claude_status" "Claude Stop should mark the current pane done"
assert_eq "claude" "$claude_agent" "Claude hook should persist the pane agent name"
claude_message_file=$(find "$PANE_DIR/conversations" -type f -name '*.last-message' -print -quit)
claude_message="$(cat "$claude_message_file")"
assert_eq "Exact Claude response." "$claude_message" "Claude Stop should cache the exact assistant response"

echo "done" > "$PANE_DIR/mixed-hooks_%13.status"
run_hook "Stop" "%3"
session_status="$(cat "$STATUS_DIR/mixed-hooks.status")"
assert_eq "done" "$session_status" "Session should become done once all tracked panes are done"

run_hook "SessionEnd" "%3" \
    '{"hook_event_name":"SessionEnd","session_id":"claude-session-456","reason":"other"}'
[ ! -f "$PANE_DIR/mixed-hooks_%3.conversation" ] || {
    echo "Assertion failed: Claude SessionEnd should clear its matching conversation identity" >&2
    exit 1
}

echo "Claude multi-pane hook regression checks passed"
