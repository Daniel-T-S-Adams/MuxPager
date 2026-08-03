#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile \
    "$PROJECT_ROOT/questions/store.py" \
    "$PROJECT_ROOT/questions/mcp_server.py" \
    "$PROJECT_ROOT/questions/mark_local_answer.py" \
    "$PROJECT_ROOT/telegram/bridge.py" \
    "$PROJECT_ROOT/telegram/repositories.py" \
    "$PROJECT_ROOT/scripts/manage-agent-mcp.py" \
    "$PROJECT_ROOT/scripts/manage-user-config.py" \
    "$PROJECT_ROOT/tests/test_no_secrets.py"
python3 "$PROJECT_ROOT/tests/test_no_secrets.py"
python3 -m unittest -v "$PROJECT_ROOT/tests/test_choice_store.py"
python3 -m unittest -v "$PROJECT_ROOT/tests/test_manage_agent_mcp.py"
python3 -m unittest -v "$PROJECT_ROOT/tests/test_muxpager_choice_mcp.py"
python3 -m unittest -v "$PROJECT_ROOT/tests/test_repository_catalog.py"
python3 -m unittest -v "$PROJECT_ROOT/tests/test_tmux_telegram_bridge.py"

bash -n \
    "$PROJECT_ROOT/install.sh" \
    "$PROJECT_ROOT/uninstall.sh" \
    "$PROJECT_ROOT/bin/muxpager-agent-list" \
    "$PROJECT_ROOT/telegram/setup.sh" \
    "$PROJECT_ROOT/scripts/doctor.sh" \
    "$PROJECT_ROOT/scripts/new-session-picker.sh" \
    "$PROJECT_ROOT/scripts/new-session.sh" \
    "$PROJECT_ROOT/scripts/setup-runtime-links.sh" \
    "$PROJECT_ROOT/plugins/tmux-agent-status/hooks/cache-conversation-id.sh" \
    "$PROJECT_ROOT/plugins/tmux-agent-status/hooks/cache-user-prompt.sh" \
    "$PROJECT_ROOT/plugins/tmux-agent-status/hooks/choice-redirect.sh"

"$PROJECT_ROOT/tests/test_path_portability.sh"
"$PROJECT_ROOT/tests/test_install_preflight.sh"
"$PROJECT_ROOT/tests/test_installer.sh"
"$PROJECT_ROOT/tests/test_new_session_picker.sh"
"$PROJECT_ROOT/tests/test_new_session_worktree.sh"

passed=0
failed=0
for test_file in "$PROJECT_ROOT"/plugins/tmux-agent-status/tests/*.sh; do
    if bash "$test_file"; then
        passed=$((passed + 1))
    else
        printf 'FAILED: %s\n' "$test_file" >&2
        failed=$((failed + 1))
    fi
done
printf 'Plugin regressions: %s passed, %s failed\n' "$passed" "$failed"
test "$failed" -eq 0

if command -v systemd-analyze >/dev/null 2>&1; then
    VERIFY_UNIT_ROOT="$(mktemp -d)"
    trap 'rm -rf "$VERIFY_UNIT_ROOT"' EXIT
    ln -s \
        "$PROJECT_ROOT/telegram/bridge.py" \
        "$VERIFY_UNIT_ROOT/tmux-telegram-bridge"
    sed \
        "s|%h/.local/bin/tmux-telegram-bridge|$VERIFY_UNIT_ROOT/tmux-telegram-bridge|" \
        "$PROJECT_ROOT/telegram/tmux-telegram-bridge.service" \
        >"$VERIFY_UNIT_ROOT/tmux-telegram-bridge.service"
    systemd-analyze --user verify \
        "$VERIFY_UNIT_ROOT/tmux-telegram-bridge.service"
    rm -rf "$VERIFY_UNIT_ROOT"
    trap - EXIT
fi

echo "All project tests passed."
