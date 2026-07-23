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
MODULE_PATH = REPO_ROOT / "questions/mcp_server.py"
SPEC = importlib.util.spec_from_file_location("muxpager_choice_mcp", MODULE_PATH)
assert SPEC and SPEC.loader
mcp_server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mcp_server
SPEC.loader.exec_module(mcp_server)


class ChoiceMcpTests(unittest.TestCase):
    def test_tool_is_one_bounded_single_choice_question(self) -> None:
        definition = mcp_server.tool_definition()
        schema = definition["inputSchema"]

        self.assertEqual("ask_choice", definition["name"])
        self.assertEqual(["question", "options"], schema["required"])
        self.assertEqual(2, schema["properties"]["options"]["minItems"])
        self.assertEqual(6, schema["properties"]["options"]["maxItems"])
        self.assertFalse(schema["additionalProperties"])

    def test_tool_call_creates_a_durable_request_for_the_current_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "MUXPAGER_AGENT": "claude",
                "MUXPAGER_CHOICE_DIR": str(Path(temporary) / "choices"),
            }
            pane = {
                "pane_id": "%9",
                "session": "work",
                "window_index": "3",
                "pane_index": "0",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch.object(mcp_server, "resolve_pane", return_value=pane):
                    result = mcp_server.create_choice(
                        {
                            "question": "Which branch?",
                            "options": [
                                {"label": "Main", "description": "Stable"},
                                {"label": "Feature", "description": "Newest"},
                            ],
                        }
                    )

            self.assertFalse(result["isError"])
            self.assertEqual("waiting_for_user", result["structuredContent"]["status"])
            files = list((Path(temporary) / "choices").glob("*.json"))
            self.assertEqual(1, len(files))
            stored = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual("claude", stored["agent"])
            self.assertEqual("%9", stored["pane_id"])

    def test_stdio_handshake_lists_the_tool(self) -> None:
        messages = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": {},
                    }
                ),
                "",
            ]
        )
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input=messages,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        responses = [json.loads(line) for line in result.stdout.splitlines()]

        self.assertEqual("2024-11-05", responses[0]["result"]["protocolVersion"])
        self.assertEqual(
            "ask_choice", responses[1]["result"]["tools"][0]["name"]
        )
        self.assertEqual("", result.stderr)

    def test_native_choice_hooks_redirect_both_agents_to_muxpager(self) -> None:
        redirect = REPO_ROOT / "plugins/tmux-agent-status/hooks/choice-redirect.sh"
        result = subprocess.run(
            ["bash", str(redirect)],
            input="{}\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        payload = json.loads(result.stdout)
        decision = payload["hookSpecificOutput"]
        self.assertEqual("deny", decision["permissionDecision"])
        self.assertIn("MuxPager MCP ask_choice", decision["permissionDecisionReason"])

        codex = json.loads(
            (REPO_ROOT / "config/codex-hooks.json").read_text(encoding="utf-8")
        )
        claude = json.loads(
            (REPO_ROOT / "config/claude-hooks.json").read_text(encoding="utf-8")
        )
        codex_matchers = {
            entry.get("matcher") for entry in codex["hooks"]["PreToolUse"]
        }
        claude_matchers = {
            entry.get("matcher") for entry in claude["hooks"]["PreToolUse"]
        }
        self.assertIn(
            r"^(request_user_input|functions\.request_user_input)$",
            codex_matchers,
        )
        self.assertIn(r"^AskUserQuestion$", claude_matchers)
        self.assertIn(
            "mcp__muxpager__ask_choice",
            claude["permissions"]["allow"],
        )


if __name__ == "__main__":
    unittest.main()
