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

python3 - "$TEST_HOME" "$PROJECT_ROOT" <<'PY'
import json
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
root = Path(sys.argv[2])
(home / ".tmux.conf").write_text(
    "set -g status-position top\n\n"
    "# >>> tmux-agent-console managed configuration >>>\n"
    'source-file "/old/checkout/.tmux.conf"\n'
    "# <<< tmux-agent-console managed configuration <<<\n",
    encoding="utf-8",
)
(home / ".claude" / "settings.json").write_text(
    json.dumps(
        {
            "model": "existing-model",
            "permissions": {"allow": ["WebFetch(domain:example.com)"]},
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "printf unrelated-hook",
                            },
                            {
                                "type": "command",
                                "command": (
                                    "bash \"$HOME/.local/libexec/"
                                    "tmux-agent-console/hooks/better-hook.sh\" Stop"
                                ),
                            }
                        ]
                    }
                ]
            },
        }
    ),
    encoding="utf-8",
)
legacy_state = home / ".config" / "tmux-agent-console" / "install.json"
legacy_state.parent.mkdir(parents=True)
legacy_state.write_text(
    json.dumps(
        {
            "project_root": str(root),
            "tmux": {
                "entry_path": str(home / ".tmux.conf"),
                "write_path": str(home / ".tmux.conf"),
                "original_kind": "regular",
            },
            "codex": {
                "entry_path": str(home / ".codex/hooks.json"),
                "write_path": str(home / ".codex/hooks.json"),
                "original_kind": "missing",
            },
            "claude": {
                "entry_path": str(home / ".claude/settings.json"),
                "write_path": str(home / ".claude/settings.json"),
                "original_kind": "regular",
            },
        }
    ),
    encoding="utf-8",
)
legacy_hooks = home / ".local" / "libexec" / "tmux-agent-console" / "hooks"
legacy_hooks.parent.mkdir(parents=True)
os.symlink(root / "plugins" / "tmux-agent-status" / "hooks", legacy_hooks)
PY

HOME="$TEST_HOME" \
XDG_CONFIG_HOME="$TEST_HOME/.config" \
TMUX_AGENT_SYSTEMCTL_BIN=/bin/true \
TMUX_AGENT_SKIP_LIVE_RELOAD=1 \
    "$PROJECT_ROOT/install.sh"
HOME="$TEST_HOME" \
XDG_CONFIG_HOME="$TEST_HOME/.config" \
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
assert tmux_config.count("# >>> muxpager managed configuration >>>") == 1
assert "tmux-agent-console managed configuration" not in tmux_config
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
        if "/.local/libexec/muxpager/hooks/" in command
    ]
    assert len(managed) == 5

claude = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
assert claude["model"] == "existing-model"
assert "WebFetch(domain:example.com)" in claude["permissions"]["allow"]
assert "mcp__muxpager__ask_choice" in claude["permissions"]["allow"]
assert any(
    hook.get("command") == "printf unrelated-hook"
    for entry in claude["hooks"]["Stop"]
    for hook in entry["hooks"]
)

env_file = home / ".config/tmux-telegram-bridge/env"
assert env_file.exists()
assert oct(env_file.stat().st_mode & 0o777) == "0o600"
assert (home / ".local/bin/tmux-telegram-bridge").resolve() == (
    root / "telegram/bridge.py"
)
assert (home / ".local/bin/muxpager-choice-server").resolve() == (
    root / "questions/mcp_server.py"
)
assert (home / ".local/libexec/muxpager/questions").resolve() == (
    root / "questions"
)
assert not (home / ".config/tmux-agent-console/install.json").exists()
assert (home / ".config/muxpager/install.json").exists()
assert not (home / ".local/libexec/tmux-agent-console/hooks").exists()
PY

HOME="$TEST_HOME" \
XDG_CONFIG_HOME="$TEST_HOME/.config" \
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
assert claude["permissions"]["allow"] == ["WebFetch(domain:example.com)"]
assert "/.local/libexec/muxpager/hooks/" not in json.dumps(claude)
assert "printf unrelated-hook" in json.dumps(claude)

assert not (home / ".codex/hooks.json").exists()
assert (home / ".config/tmux-telegram-bridge/env").exists()
assert not (home / ".local/bin/tmux-telegram-bridge").exists()
assert not (home / ".local/bin/muxpager-choice-server").exists()
assert not (home / ".local/libexec/muxpager/questions").exists()
PY

echo "unified installer checks passed"
