from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from questions.store import ChoiceStore, ChoiceStoreError


class ChoiceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ChoiceStore(Path(self.temporary.name) / "choices")

    def create(self, **overrides):
        arguments = {
            "pane_id": "%2",
            "session": "main",
            "window_index": "0",
            "pane_index": "1",
            "agent": "codex",
            "question": "Which deployment should I use?",
            "options": [
                {"label": "Staging", "description": "Safer test environment"},
                {"label": "Production", "description": "Live environment"},
            ],
            "now": 100.0,
        }
        arguments.update(overrides)
        return self.store.create(**arguments)

    def test_create_normalizes_options_and_uses_private_files(self) -> None:
        choice = self.create()

        self.assertEqual("pending", choice["state"])
        self.assertEqual("0", choice["options"][0]["id"])
        self.assertEqual("1", choice["options"][1]["id"])
        self.assertEqual(0o700, self.store.root.stat().st_mode & 0o777)
        path = self.store.root / f"{choice['id']}.json"
        self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_only_one_active_question_is_kept_per_agent_topic(self) -> None:
        first = self.create()
        second = self.create(question="Choose a database", now=101.0)

        self.assertEqual("superseded", self.store.get(first["id"])["state"])
        self.assertEqual(second["id"], self.store.active_for_topic(second["topic_key"])["id"])

    def test_local_prompt_wins_before_a_button_is_selected(self) -> None:
        choice = self.create(agent="claude")

        changed = self.store.mark_local_answer("%2", "claude", now=102.0)

        self.assertEqual([choice["id"]], [item["id"] for item in changed])
        self.assertEqual("answered_locally", self.store.get(choice["id"])["state"])

    def test_local_submit_acknowledges_a_bridge_delivery(self) -> None:
        choice = self.create()
        self.store.update(
            choice["id"],
            lambda current: {
                **current,
                "state": "delivering",
                "selected_option_id": "0",
            },
        )

        self.store.mark_local_answer("%2", "codex", now=103.0)

        self.assertEqual("delivered", self.store.get(choice["id"])["state"])

    def test_invalid_questions_are_rejected(self) -> None:
        with self.assertRaises(ChoiceStoreError):
            self.create(options=[{"label": "Only", "description": ""}])
        with self.assertRaises(ChoiceStoreError):
            self.create(agent="bash")
        with self.assertRaises(ChoiceStoreError):
            self.create(
                options=[
                    {"label": "Same", "description": ""},
                    {"label": "same", "description": ""},
                ]
            )

    def test_prune_removes_only_old_final_requests(self) -> None:
        old = self.create(now=10.0)
        self.store.mark_local_answer("%2", now=11.0)
        active = self.create(now=20.0)

        removed = self.store.prune(now=100.0, retain_seconds=50)

        self.assertEqual(1, removed)
        self.assertIsNone(self.store.get(old["id"]))
        self.assertIsNotNone(self.store.get(active["id"]))


if __name__ == "__main__":
    unittest.main()
