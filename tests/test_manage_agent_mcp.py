from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/manage-agent-mcp.py"
SPEC = importlib.util.spec_from_file_location("manage_agent_mcp", MODULE_PATH)
assert SPEC and SPEC.loader
manage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manage
SPEC.loader.exec_module(manage)


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class ManageAgentMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.server = self.home / ".local/bin/muxpager-choice-server"
        self.server.parent.mkdir(parents=True)
        self.server.touch()

    def test_codex_install_uses_a_scoped_agent_environment(self) -> None:
        calls = []

        def fake_run(command):
            calls.append(command)
            if command[:3] == ["codex", "mcp", "get"]:
                return completed(command, returncode=1)
            return completed(command)

        with mock.patch.object(manage, "run", side_effect=fake_run):
            result = manage.install_codex(self.server)

        self.assertEqual("configured", result)
        self.assertEqual(
            [
                "codex",
                "mcp",
                "add",
                "muxpager",
                "--env",
                "MUXPAGER_AGENT=codex",
                "--",
                str(self.server),
            ],
            calls[-1],
        )

    def test_claude_install_uses_user_scope(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            with mock.patch.object(
                manage,
                "run",
                return_value=completed(["claude", "mcp", "add"]),
            ) as run:
                result = manage.install_claude(self.server)

        self.assertEqual("configured", result)
        command = run.call_args.args[0]
        self.assertEqual(["claude", "mcp", "add", "--scope", "user"], command[:5])
        self.assertIn("MUXPAGER_AGENT=claude", command)
        self.assertEqual(str(self.server), command[-1])

    def test_unrelated_codex_entry_is_never_replaced(self) -> None:
        entry = {
            "transport": {
                "type": "stdio",
                "command": "/somebody/elses/server",
                "args": [],
                "env": {},
            }
        }
        with mock.patch.object(
            manage,
            "run",
            return_value=completed(
                ["codex", "mcp", "get"], stdout=json.dumps(entry)
            ),
        ):
            with self.assertRaises(manage.McpConfigError):
                manage.install_codex(self.server)


if __name__ == "__main__":
    unittest.main()
