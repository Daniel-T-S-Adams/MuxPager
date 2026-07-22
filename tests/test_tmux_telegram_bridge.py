from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/tmux-telegram-bridge.py"
SPEC = importlib.util.spec_from_file_location("tmux_telegram_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class TelegramBridgeTests(unittest.TestCase):
    def test_parse_agent_rows_rejects_shells_and_invalid_rows(self) -> None:
        output = (
            "%2\ttr\t0\tcodex\t1\tcodex\tdone\n"
            "%3\tplain\t0\tbash\t0\tbash\tnoagent\n"
            "broken\n"
        )
        panes = bridge.parse_agent_rows(output)
        self.assertEqual(1, len(panes))
        self.assertEqual("%2", panes[0].pane_id)
        self.assertEqual("tr › codex › codex #1", panes[0].label)

    def test_clean_capture_removes_prompt_and_footer(self) -> None:
        captured = "Old line\n\n• Final answer\n─ Worked for 2m ─\n\n› Run /review\n\ngpt-5.6-sol"
        self.assertEqual("Old line\n\n• Final answer", bridge.clean_capture(captured))

    def test_cached_exact_message_is_preferred_for_waiting_agent(self) -> None:
        pane = bridge.AgentPane("%2", "tr", "0", "codex", "1", "codex", "done")
        with tempfile.TemporaryDirectory() as temporary:
            client = bridge.TmuxClient(REPO_ROOT)
            client.message_dir = Path(temporary)
            (client.message_dir / "tr_%2.last-message").write_text(
                "Exact final response.\n", encoding="utf-8"
            )
            with mock.patch.object(client, "capture") as capture:
                text, source = client.last_output(pane)
            self.assertEqual("Exact final response.", text)
            self.assertEqual("Last agent message", source)
            capture.assert_not_called()

    def test_send_message_uses_a_private_buffer_without_a_shell(self) -> None:
        client = bridge.TmuxClient(REPO_ROOT)
        calls: list[list[str]] = []
        loaded_text: list[str] = []

        def fake_run(args, **kwargs):
            calls.append(list(args))
            if args[1] == "display-message":
                return subprocess.CompletedProcess(args, 0, stdout="0\t%2\n", stderr="")
            if args[1] == "load-buffer":
                loaded_text.append(Path(args[-1]).read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        dangerous_text = "$(touch /tmp/should-not-run); `id`; hello"
        with mock.patch.object(bridge.subprocess, "run", side_effect=fake_run):
            client.send_message("%2", dangerous_text)

        self.assertEqual([dangerous_text], loaded_text)
        self.assertFalse(any("shell=True" in str(call) for call in calls))
        self.assertIn("paste-buffer", [call[1] for call in calls])
        self.assertEqual("send-keys", calls[-1][1])


if __name__ == "__main__":
    unittest.main()
