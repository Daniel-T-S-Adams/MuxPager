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
            echo "codex-hooks"
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
    local payload=""
    if [ "$#" -ge 2 ]; then
        payload="$2"
    else
        printf -v payload '{"hook_event_name":"%s"}' "$hook_name"
    fi

    printf '%s\n' "$payload" | \
        PATH="$FAKE_BIN:$PATH" \
        HOME="$TEST_HOME" \
        TMUX="/tmp/tmux-test,4242,0" \
        TMUX_PANE="%9" \
        "$REPO_DIR/hooks/codex-hook.sh" "$hook_name"
}

run_hook "SessionStart" \
    '{"hook_event_name":"SessionStart","session_id":"codex-session-123","source":"startup"}'
session_status="$(cat "$STATUS_DIR/codex-hooks.status")"
pane_status="$(cat "$PANE_DIR/codex-hooks_%9.status")"
agent_name="$(cat "$PANE_DIR/codex-hooks_%9.agent")"
conversation_agent="$(sed -n '1p' "$PANE_DIR/codex-hooks_%9.conversation")"
conversation_id="$(sed -n '2p' "$PANE_DIR/codex-hooks_%9.conversation")"
assert_eq "done" "$session_status" "SessionStart should seed the session as done"
assert_eq "done" "$pane_status" "SessionStart should seed the pane as done"
assert_eq "codex" "$agent_name" "Codex hooks should persist the pane agent name"
assert_eq "codex" "$conversation_agent" "Codex hooks should identify the conversation agent"
assert_eq "codex-session-123" "$conversation_id" "Codex hooks should persist the stable session ID"
[ -f "$REFRESH_FILE" ] || { echo "Assertion failed: SessionStart should touch sidebar refresh marker" >&2; exit 1; }
session_start_refresh=$(<"$REFRESH_FILE")

echo "parked" > "$STATUS_DIR/codex-hooks.status"
echo "parked" > "$PANE_DIR/codex-hooks_%9.status"
: > "$PARKED_DIR/codex-hooks.parked"
: > "$PARKED_DIR/codex-hooks_%9.parked"
: > "$PARKED_DIR/codex-hooks_%10.parked"
run_hook "UserPromptSubmit"
session_status="$(cat "$STATUS_DIR/codex-hooks.status")"
pane_status="$(cat "$PANE_DIR/codex-hooks_%9.status")"
assert_eq "working" "$session_status" "UserPromptSubmit should mark the session working"
assert_eq "working" "$pane_status" "UserPromptSubmit should mark the pane working"
[ -f "$REFRESH_FILE" ] || { echo "Assertion failed: UserPromptSubmit should leave a sidebar refresh marker" >&2; exit 1; }
prompt_refresh=$(<"$REFRESH_FILE")
if [ "$prompt_refresh" = "$session_start_refresh" ]; then
    echo "Assertion failed: consecutive Codex hooks should write distinct refresh tokens" >&2
    exit 1
fi
if [ -f "$PARKED_DIR/codex-hooks.parked" ]; then
    echo "Assertion failed: UserPromptSubmit should unpark the session" >&2
    exit 1
fi
if [ -f "$PARKED_DIR/codex-hooks_%9.parked" ] || [ -f "$PARKED_DIR/codex-hooks_%10.parked" ]; then
    echo "Assertion failed: UserPromptSubmit should clear per-pane parked overrides when the whole session was parked" >&2
    exit 1
fi

echo "parked" > "$PANE_DIR/codex-hooks_%9.status"
: > "$PARKED_DIR/codex-hooks_%9.parked"
run_hook "UserPromptSubmit"
if [ -f "$PARKED_DIR/codex-hooks_%9.parked" ]; then
    echo "Assertion failed: UserPromptSubmit should clear the current pane parked override" >&2
    exit 1
fi

run_hook "UserPromptSubmit" \
    '{"hook_event_name":"UserPromptSubmit","session_id":"codex-session-123","prompt":"Exact local Codex prompt.\nSecond line."}'
prompt_file=$(find "$PANE_DIR/conversations" -type f -name '*.prompt' -print -quit)
[ -n "$prompt_file" ] || {
    echo "Assertion failed: Codex UserPromptSubmit should queue the exact local prompt" >&2
    exit 1
}
assert_eq $'Exact local Codex prompt.\nSecond line.' "$(cat "$prompt_file")" \
    "Codex UserPromptSubmit should preserve the complete prompt"
rm -rf "$PANE_DIR/conversations"

telegram_prompt_hash=$(printf '%s' 'Prompt from Telegram' | sha256sum | awk '{print $1}')
printf '{"prompt_sha256":"%s","created_at":%s}\n' \
    "$telegram_prompt_hash" "$(date +%s)" \
    > "$PANE_DIR/codex-hooks_%9.telegram-prompt"
run_hook "UserPromptSubmit" \
    '{"hook_event_name":"UserPromptSubmit","session_id":"codex-session-123","prompt":"Prompt from Telegram"}'
if find "$PANE_DIR" -type f -name '*.prompt' -print -quit | grep -q .; then
    echo "Assertion failed: Telegram-originated Codex prompt should not be queued" >&2
    exit 1
fi
if [ -f "$PANE_DIR/codex-hooks_%9.telegram-prompt" ]; then
    echo "Assertion failed: matched Telegram prompt marker should be consumed" >&2
    exit 1
fi

echo "parked" > "$STATUS_DIR/codex-hooks.status"
rm -f "$PANE_DIR/codex-hooks_%9.status"
: > "$PARKED_DIR/codex-hooks.parked"
run_hook "PreToolUse"
session_status="$(cat "$STATUS_DIR/codex-hooks.status")"
assert_eq "parked" "$session_status" "PreToolUse should not unpark explicitly parked sessions"
if [ ! -f "$PARKED_DIR/codex-hooks.parked" ]; then
    echo "Assertion failed: PreToolUse should preserve the parked marker" >&2
    exit 1
fi

rm -f "$PARKED_DIR/codex-hooks.parked"
run_hook "Stop" '{"hook_event_name":"Stop","session_id":"codex-session-123","last_assistant_message":"Exact Codex response.\nSecond line."}'
session_status="$(cat "$STATUS_DIR/codex-hooks.status")"
pane_status="$(cat "$PANE_DIR/codex-hooks_%9.status")"
assert_eq "done" "$session_status" "Stop should mark the session done"
assert_eq "done" "$pane_status" "Stop should mark the pane done"
last_message_file=$(find "$PANE_DIR/conversations" -type f -name '*.last-message' -print -quit)
last_message="$(cat "$last_message_file")"
assert_eq $'Exact Codex response.\nSecond line.' "$last_message" "Codex Stop should cache the exact assistant response"

run_hook "SessionEnd" \
    '{"hook_event_name":"SessionEnd","session_id":"different-session","reason":"other"}'
[ -f "$PANE_DIR/codex-hooks_%9.conversation" ] || {
    echo "Assertion failed: a stale Codex SessionEnd must not clear a newer conversation" >&2
    exit 1
}
run_hook "SessionEnd" \
    '{"hook_event_name":"SessionEnd","session_id":"codex-session-123","reason":"other"}'
[ ! -f "$PANE_DIR/codex-hooks_%9.conversation" ] || {
    echo "Assertion failed: Codex SessionEnd should clear its matching conversation identity" >&2
    exit 1
}

echo "Codex hook lifecycle checks passed"
