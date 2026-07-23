#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"

cleanup() {
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

TEST_HOME="$TEST_ROOT/home"
mkdir -p "$TEST_HOME/.claude"

python3 - "$TEST_HOME" <<'PY'
import json
import sys
from pathlib import Path

home = Path(sys.argv[1])
(home / ".tmux.conf").write_text("set -g status-position top\n", encoding="utf-8")
(home / ".claude" / "settings.json").write_text(
    json.dumps(
        {
            "model": "existing-model",
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "printf unrelated-hook",
                            }
                        ]
                    }
                ]
            },
        }
    ),
    encoding="utf-8",
)
PY

HOME="$TEST_HOME" \
TMUX_AGENT_SYSTEMCTL_BIN=/bin/true \
TMUX_AGENT_SKIP_LIVE_RELOAD=1 \
    "$PROJECT_ROOT/install.sh"
HOME="$TEST_HOME" \
TMUX_AGENT_SYSTEMCTL_BIN=/bin/true \
TMUX_AGENT_SKIP_LIVE_RELOAD=1 \
    "$PROJECT_ROOT/install.sh"

python3 - "$TEST_HOME" "$PROJECT_ROOT" <<'PY'
import json
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
root = Path(sys.argv[2])

tmux_config = (home / ".tmux.conf").read_text(encoding="utf-8")
assert "set -g status-position top" in tmux_config
assert tmux_config.count("# >>> tmux-agent-console managed configuration >>>") == 1
assert f'source-file "{root}/.tmux.conf"' in tmux_config

for path in (home / ".codex/hooks.json", home / ".claude/settings.json"):
    payload = json.loads(path.read_text(encoding="utf-8"))
    commands = [
        hook.get("command", "")
        for entries in payload.get("hooks", {}).values()
        for entry in entries
        for hook in entry.get("hooks", [])
    ]
    managed = [
        command
        for command in commands
        if "/.local/libexec/tmux-agent-console/hooks/" in command
    ]
    assert len(managed) == 4

claude = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
assert claude["model"] == "existing-model"
assert any(
    hook.get("command") == "printf unrelated-hook"
    for entry in claude["hooks"]["Stop"]
    for hook in entry["hooks"]
)

env_file = home / ".config/tmux-telegram-bridge/env"
assert env_file.exists()
assert oct(env_file.stat().st_mode & 0o777) == "0o600"
assert (home / ".local/bin/tmux-telegram-bridge").resolve() == (
    root / "scripts/tmux-telegram-bridge.py"
)
PY

HOME="$TEST_HOME" \
TMUX_AGENT_SYSTEMCTL_BIN=/bin/true \
    "$PROJECT_ROOT/uninstall.sh"

python3 - "$TEST_HOME" <<'PY'
import json
import sys
from pathlib import Path

home = Path(sys.argv[1])
tmux_config = (home / ".tmux.conf").read_text(encoding="utf-8")
assert tmux_config.strip() == "set -g status-position top"

claude = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
assert claude["model"] == "existing-model"
assert "tmux-agent-console" not in json.dumps(claude)
assert "printf unrelated-hook" in json.dumps(claude)

assert not (home / ".codex/hooks.json").exists()
assert (home / ".config/tmux-telegram-bridge/env").exists()
assert not (home / ".local/bin/tmux-telegram-bridge").exists()
PY

echo "unified installer checks passed"
