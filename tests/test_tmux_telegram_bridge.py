from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "telegram/bridge.py"
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
        self.assertEqual("tr · codex #1", panes[0].topic_name)

    def test_topic_name_ignores_tmux_copy_mode_window_name(self) -> None:
        pane = bridge.AgentPane(
            "%2", "tr", "0", "[tmux]", "1", "codex", "working"
        )

        self.assertEqual("tr · codex #1", pane.topic_name)

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


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_thread_id = 100
        self.next_message_id = 1000
        self.member_count = 2

    def call(self, method, params=None, http_timeout=30):
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "getMe":
            return {"id": 9001, "is_bot": True}
        if method == "getChat":
            return {
                "id": params["chat_id"],
                "type": "supergroup",
                "title": "My tmux agents",
                "is_forum": True,
            }
        if method == "getChatMember":
            return {
                "status": "administrator",
                "can_manage_topics": True,
            }
        if method == "getChatMemberCount":
            return self.member_count
        if method == "createForumTopic":
            self.next_thread_id += 1
            return {
                "message_thread_id": self.next_thread_id,
                "name": params["name"],
            }
        if method == "sendMessage":
            self.next_message_id += 1
            return {
                "message_id": self.next_message_id,
                "chat": {"id": params["chat_id"]},
                "text": params["text"],
            }
        return True


class FakeTmux:
    def __init__(self, panes) -> None:
        self.panes = list(panes)
        self.versions = {pane.topic_key: "v1" for pane in panes}
        self.outputs = {
            pane.topic_key: ("Exact final response.", "Last agent message")
            for pane in panes
        }
        self.sent: list[tuple[str, str]] = []

    def agents(self):
        return list(self.panes)

    def message_version(self, pane):
        return self.versions.get(pane.topic_key, "")

    def last_output(self, pane):
        return self.outputs[pane.topic_key]

    def send_message(self, pane_id, text):
        self.sent.append((pane_id, text))


class ForumTopicBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pane = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "working"
        )
        self.telegram = FakeTelegram()
        self.tmux = FakeTmux([self.pane])
        self.instance = bridge.Bridge(
            self.telegram,
            self.tmux,
            allowed_user_id=42,
            state_path=Path(self.temporary.name) / "state.json",
            close_after=0,
        )
        self.control_message = {
            "message_id": 1,
            "from": {"id": 42},
            "chat": {
                "id": -100123,
                "type": "supergroup",
                "title": "My tmux agents",
                "is_forum": True,
            },
            "message_thread_id": 1,
            "text": "/setup",
        }

    def configure(self) -> int:
        self.instance._handle_message(self.control_message)
        record = self.instance.state["topics"][self.pane.topic_key]
        return record["thread_id"]

    def create_choice(self):
        return self.instance.choice_store.create(
            pane_id=self.pane.pane_id,
            session=self.pane.session,
            window_index=self.pane.window_index,
            pane_index=self.pane.pane_index,
            agent=self.pane.agent,
            question="Which environment should I use?",
            options=[
                {"label": "Staging", "description": "Run safely first"},
                {"label": "Production", "description": "Use live data"},
            ],
        )

    @staticmethod
    def callback(choice, thread_id, option_id="0", user_id=42):
        return {
            "id": "callback-1",
            "from": {"id": user_id},
            "data": f"mq:{choice['id']}:{option_id}",
            "message": {
                "message_id": choice["telegram_message_id"],
                "message_thread_id": thread_id,
                "chat": {"id": -100123, "type": "supergroup"},
            },
        }

    def test_setup_creates_one_topic_for_each_live_agent(self) -> None:
        thread_id = self.configure()

        self.assertEqual(-100123, self.instance.state["group_chat_id"])
        self.assertEqual(101, thread_id)
        methods = [method for method, _ in self.telegram.calls]
        self.assertIn("editGeneralForumTopic", methods)
        self.assertIn("createForumTopic", methods)
        create = next(
            params
            for method, params in self.telegram.calls
            if method == "createForumTopic"
        )
        self.assertEqual("tr · codex #1", create["name"])

    def test_setup_rejects_a_group_with_other_members(self) -> None:
        self.telegram.member_count = 3

        self.instance._handle_message(self.control_message)

        self.assertIsNone(self.instance._group_chat_id())
        self.assertNotIn(
            "createForumTopic", [method for method, _ in self.telegram.calls]
        )
        warning = next(
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        )
        self.assertIn("exactly two members", warning)

    def test_topic_message_is_routed_only_to_its_mapped_pane(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        message = {
            **self.control_message,
            "message_id": 2,
            "message_thread_id": thread_id,
            "text": "Please run the tests",
        }

        self.instance._handle_message(message)

        self.assertEqual([("%2", "Please run the tests")], self.tmux.sent)
        self.assertIn("setMessageReaction", [method for method, _ in self.telegram.calls])

    def test_completed_reply_is_posted_as_a_fresh_topic_message(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        done = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "done"
        )
        self.tmux.panes = [done]
        self.tmux.versions[done.topic_key] = "v2"

        self.instance._sync_topics()

        notifications = [
            params
            for method, params in self.telegram.calls
            if method == "sendMessage"
            and params.get("message_thread_id") == thread_id
            and not params.get("disable_notification")
        ]
        self.assertEqual(1, len(notifications))
        self.assertIn("Exact final response.", notifications[0]["text"])

    def test_topic_mapping_survives_a_changed_tmux_pane_id(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        replacement = bridge.AgentPane(
            "%99", "tr", "0", "codex", "1", "codex", "working"
        )
        self.tmux.panes = [replacement]

        self.instance._sync_topics()

        record = self.instance.state["topics"][replacement.topic_key]
        self.assertEqual(thread_id, record["thread_id"])
        self.assertEqual("%99", record["pane_id"])
        self.assertNotIn(
            "createForumTopic", [method for method, _ in self.telegram.calls]
        )

    def test_missing_agent_topic_is_closed_not_deleted(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        self.tmux.panes = []

        self.instance._sync_topics()

        close_calls = [
            params
            for method, params in self.telegram.calls
            if method == "closeForumTopic"
        ]
        self.assertEqual(
            [{"chat_id": -100123, "message_thread_id": thread_id}], close_calls
        )
        record = self.instance.state["topics"][self.pane.topic_key]
        self.assertTrue(record["closed"])

    def test_unauthorized_group_message_is_ignored(self) -> None:
        thread_id = self.configure()
        message = {
            **self.control_message,
            "message_id": 3,
            "message_thread_id": thread_id,
            "from": {"id": 999},
            "text": "Do something unsafe",
        }

        self.instance._handle_message(message)

        self.assertEqual([], self.tmux.sent)

    def test_choice_is_published_as_inline_buttons_in_the_agent_topic(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        choice = self.create_choice()

        self.instance._sync_topics()

        published = self.instance.choice_store.get(choice["id"])
        self.assertEqual("published", published["state"])
        messages = [
            params
            for method, params in self.telegram.calls
            if method == "sendMessage" and "reply_markup" in params
        ]
        self.assertEqual(1, len(messages))
        self.assertEqual(thread_id, messages[0]["message_thread_id"])
        buttons = messages[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual(["Staging", "Production", "Cancel"], [row[0]["text"] for row in buttons])

    def test_button_selection_is_delivered_as_the_next_user_message(self) -> None:
        thread_id = self.configure()
        choice = self.create_choice()
        self.instance._sync_topics()
        published = self.instance.choice_store.get(choice["id"])
        self.tmux.panes = [
            bridge.AgentPane(
                "%2", "tr", "0", "codex", "1", "codex", "done"
            )
        ]
        self.telegram.calls.clear()

        self.instance._handle_callback_query(self.callback(published, thread_id))

        self.assertEqual(1, len(self.tmux.sent))
        pane_id, prompt = self.tmux.sent[0]
        self.assertEqual("%2", pane_id)
        self.assertIn("Selected answer: Staging", prompt)
        self.assertEqual(
            "delivered", self.instance.choice_store.get(choice["id"])["state"]
        )
        methods = [method for method, _ in self.telegram.calls]
        self.assertIn("answerCallbackQuery", methods)
        self.assertIn("editMessageText", methods)

    def test_button_selection_waits_while_agent_is_still_working(self) -> None:
        thread_id = self.configure()
        choice = self.create_choice()
        self.instance._sync_topics()
        published = self.instance.choice_store.get(choice["id"])

        self.instance._handle_callback_query(self.callback(published, thread_id, "1"))

        self.assertEqual([], self.tmux.sent)
        self.assertEqual(
            "selected", self.instance.choice_store.get(choice["id"])["state"]
        )

        self.tmux.panes = [
            bridge.AgentPane(
                "%2", "tr", "0", "codex", "1", "codex", "done"
            )
        ]
        self.instance._sync_topics()

        self.assertEqual(1, len(self.tmux.sent))
        self.assertIn("Selected answer: Production", self.tmux.sent[0][1])
        self.assertEqual(
            "delivered", self.instance.choice_store.get(choice["id"])["state"]
        )

    def test_unauthorized_button_press_cannot_select_or_deliver(self) -> None:
        thread_id = self.configure()
        choice = self.create_choice()
        self.instance._sync_topics()
        published = self.instance.choice_store.get(choice["id"])
        self.tmux.panes = [
            bridge.AgentPane(
                "%2", "tr", "0", "codex", "1", "codex", "done"
            )
        ]

        self.instance._handle_callback_query(
            self.callback(published, thread_id, user_id=999)
        )

        self.assertEqual([], self.tmux.sent)
        self.assertEqual(
            "published", self.instance.choice_store.get(choice["id"])["state"]
        )

    def test_ordinary_text_is_rejected_while_buttons_are_active(self) -> None:
        thread_id = self.configure()
        choice = self.create_choice()
        self.instance._sync_topics()
        self.telegram.calls.clear()
        message = {
            **self.control_message,
            "message_id": 8,
            "message_thread_id": thread_id,
            "text": "Ignore the options and continue",
        }

        self.instance._handle_message(message)

        self.assertEqual([], self.tmux.sent)
        warning = next(
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        )
        self.assertIn("waiting for a button choice", warning)
        self.assertEqual(
            "published", self.instance.choice_store.get(choice["id"])["state"]
        )

    def test_active_choice_suppresses_the_normal_completion_notification(self) -> None:
        self.configure()
        self.create_choice()
        self.telegram.calls.clear()
        done = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "done"
        )
        self.tmux.panes = [done]
        self.tmux.versions[done.topic_key] = "v2"

        self.instance._sync_topics()

        messages = [
            params
            for method, params in self.telegram.calls
            if method == "sendMessage" and not params.get("disable_notification")
        ]
        self.assertEqual(1, len(messages))
        self.assertIn("Which environment", messages[0]["text"])

    def test_choice_expires_if_the_exact_agent_pane_exits(self) -> None:
        self.configure()
        choice = self.create_choice()
        self.instance._sync_topics()
        self.tmux.panes = []

        self.instance._sync_topics()

        self.assertEqual(
            "expired", self.instance.choice_store.get(choice["id"])["state"]
        )


if __name__ == "__main__":
    unittest.main()
