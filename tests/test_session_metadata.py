from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "telegram/sessions.py"
SPEC = importlib.util.spec_from_file_location("muxpager_sessions", MODULE_PATH)
assert SPEC and SPEC.loader
sessions = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sessions
SPEC.loader.exec_module(sessions)


class SessionMetadataResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.codex_home = self.root / "codex"
        self.claude_home = self.root / "claude"
        self.resolver = sessions.SessionMetadataResolver(
            self.codex_home, self.claude_home
        )

    def write_jsonl(self, path: Path, payloads: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(payload) + "\n" for payload in payloads),
            encoding="utf-8",
        )

    def test_resolves_codex_session_metadata(self) -> None:
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        cwd = self.root / "repositories" / "MuxPager"
        self.write_jsonl(
            self.codex_home
            / "sessions/2026/08/04"
            / f"rollout-{conversation_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": conversation_id,
                        "session_id": conversation_id,
                        "cwd": str(cwd),
                    },
                }
            ],
        )

        self.assertEqual(cwd, self.resolver.resolve("codex", conversation_id))

    def test_resolves_claude_metadata_after_preamble_records(self) -> None:
        conversation_id = "b57bf8e4-59a7-4b10-a809-fa8ef9b3acee"
        cwd = self.root / "repositories" / "ClaudeProject"
        self.write_jsonl(
            self.claude_home / "projects/project" / f"{conversation_id}.jsonl",
            [
                {"type": "mode", "sessionId": conversation_id},
                {
                    "type": "user",
                    "sessionId": conversation_id,
                    "cwd": str(cwd),
                },
            ],
        )

        self.assertEqual(cwd, self.resolver.resolve("claude", conversation_id))

    def test_rejects_invalid_ids_and_mismatched_metadata(self) -> None:
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        self.write_jsonl(
            self.codex_home / "sessions" / f"rollout-{conversation_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "019fba20-0898-7383-b7df-a686a55fae85",
                        "cwd": "/tmp/wrong",
                    },
                }
            ],
        )

        self.assertIsNone(self.resolver.resolve("codex", "../../etc/passwd"))
        self.assertIsNone(self.resolver.resolve("devin", conversation_id))
        self.assertIsNone(self.resolver.resolve("codex", conversation_id))


if __name__ == "__main__":
    unittest.main()
