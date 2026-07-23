#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile \
    "$PROJECT_ROOT/telegram/bridge.py" \
    "$PROJECT_ROOT/scripts/manage-user-config.py"
python3 -m unittest -v "$PROJECT_ROOT/tests/test_tmux_telegram_bridge.py"

bash -n \
    "$PROJECT_ROOT/install.sh" \
    "$PROJECT_ROOT/uninstall.sh" \
    "$PROJECT_ROOT/bin/tmux-agent-list" \
    "$PROJECT_ROOT/telegram/setup.sh" \
    "$PROJECT_ROOT/scripts/doctor.sh" \
    "$PROJECT_ROOT/scripts/setup-runtime-links.sh"

"$PROJECT_ROOT/tests/test_path_portability.sh"
"$PROJECT_ROOT/tests/test_installer.sh"

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
    systemd-analyze --user verify \
        "$PROJECT_ROOT/telegram/tmux-telegram-bridge.service"
fi

echo "All project tests passed."
