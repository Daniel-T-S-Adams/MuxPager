from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import shlex
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
            "%2\ttr\t0\tcodex\t1\tcodex\tdone\tcodex-session-123\n"
            "%3\tplain\t0\tbash\t0\tbash\tnoagent\n"
            "broken\n"
        )
        panes = bridge.parse_agent_rows(output)
        self.assertEqual(1, len(panes))
        self.assertEqual("%2", panes[0].pane_id)
        self.assertEqual("tr › codex › codex #1", panes[0].label)
        self.assertEqual("codex-session-123", panes[0].conversation_id)
        self.assertEqual("tr · codex #1", panes[0].topic_name)

    def test_topic_name_ignores_volatile_tmux_window_names_and_hashes(self) -> None:
        for window_name in ("[tmux]", "bash", "codex"):
            pane = bridge.AgentPane(
                "%2",
                "tr",
                "0",
                window_name,
                "1",
                "codex",
                "working",
                "codex-session-123",
            )
            self.assertEqual("tr · codex #1", pane.topic_name)

        custom = bridge.AgentPane(
            "%2",
            "tr",
            "0",
            "api-refactor",
            "1",
            "codex",
            "working",
            "codex-session-123",
        )
        self.assertEqual("tr · api-refactor · codex #1", custom.topic_name)

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

    def test_cached_messages_are_scoped_to_the_agent_conversation(self) -> None:
        first = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "done", "session-one"
        )
        second = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "done", "session-two"
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = bridge.TmuxClient(REPO_ROOT)
            client.message_dir = Path(temporary)
            client._message_path(first).parent.mkdir(parents=True)
            client._message_path(first).write_text(
                "First conversation response.\n", encoding="utf-8"
            )
            client._message_path(second).write_text(
                "Second conversation response.\n", encoding="utf-8"
            )

            self.assertNotEqual(client._message_path(first), client._message_path(second))
            self.assertEqual(
                ("First conversation response.", "Last agent message"),
                client.last_output(first),
            )
            self.assertEqual(
                ("Second conversation response.", "Last agent message"),
                client.last_output(second),
            )

    def test_send_message_uses_a_private_buffer_without_a_shell(self) -> None:
        client = bridge.TmuxClient(REPO_ROOT)
        pane = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "done", "session-one"
        )
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
        with tempfile.TemporaryDirectory() as temporary:
            client.message_dir = Path(temporary)
            with mock.patch.object(client, "agents", return_value=[pane]):
                with mock.patch.object(bridge.subprocess, "run", side_effect=fake_run):
                    client.send_message(pane, dangerous_text)
            marker = json.loads(
                (client.message_dir / "tr_%2.telegram-prompt").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual([dangerous_text], loaded_text)
        self.assertEqual(
            hashlib.sha256(dangerous_text.encode("utf-8")).hexdigest(),
            marker["prompt_sha256"],
        )
        self.assertFalse(any("shell=True" in str(call) for call in calls))
        self.assertIn("paste-buffer", [call[1] for call in calls])
        self.assertEqual("send-keys", calls[-1][1])

    def test_launch_agent_creates_a_detached_tmux_session_in_repository(self) -> None:
        client = bridge.TmuxClient(REPO_ROOT)
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append(list(args))
            if args[1] == "new-session":
                return subprocess.CompletedProcess(
                    args, 0, stdout="%50\tmuxpager\t0\n", stderr=""
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with mock.patch.object(
            client, "_agent_executable", return_value="/opt/Agent Tools/codex"
        ):
            with mock.patch.object(
                client, "_launch_session_name", return_value=("muxpager", False)
            ):
                with mock.patch.object(bridge.subprocess, "run", side_effect=fake_run):
                    target = client.launch_agent(REPO_ROOT, "codex")

        self.assertEqual(bridge.LaunchTarget("%50", "muxpager", "0"), target)
        self.assertIn("-d", calls[0])
        self.assertEqual(str(REPO_ROOT), calls[0][calls[0].index("-c") + 1])
        self.assertEqual(
            [
                "tmux",
                "send-keys",
                "-t",
                "%50",
                "-l",
                "'/opt/Agent Tools/codex'",
            ],
            calls[1],
        )
        self.assertFalse(any(call == ["bash", "-c"] for call in calls))

    def test_resume_agent_uses_a_dedicated_session_and_exact_cli_id(self) -> None:
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        for agent, expected_arguments in (
            ("codex", ["resume", conversation_id]),
            ("claude", ["--resume", conversation_id]),
        ):
            with self.subTest(agent=agent):
                client = bridge.TmuxClient(REPO_ROOT)
                calls: list[list[str]] = []

                def fake_run(args, **kwargs):
                    calls.append(list(args))
                    if args[1] == "new-session":
                        return subprocess.CompletedProcess(
                            args, 0, stdout="%60\tmuxpager-019fba20\t0\n", stderr=""
                        )
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

                with mock.patch.object(
                    client, "_agent_executable", return_value=f"/opt/Agent Tools/{agent}"
                ):
                    with mock.patch.object(
                        client,
                        "_resume_session_name",
                        return_value="muxpager-019fba20",
                    ):
                        with mock.patch.object(
                            bridge.subprocess, "run", side_effect=fake_run
                        ):
                            target = client.resume_agent(
                                REPO_ROOT, agent, conversation_id
                            )

                self.assertEqual(
                    bridge.LaunchTarget("%60", "muxpager-019fba20", "0"), target
                )
                self.assertEqual("new-session", calls[0][1])
                self.assertNotIn("new-window", [call[1] for call in calls])
                self.assertEqual(
                    [
                        "tmux",
                        "send-keys",
                        "-t",
                        "%60",
                        "-l",
                        shlex.join(
                            [f"/opt/Agent Tools/{agent}", *expected_arguments]
                        ),
                    ],
                    calls[1],
                )

    def test_resume_agent_rejects_an_untrusted_session_id(self) -> None:
        client = bridge.TmuxClient(REPO_ROOT)

        with mock.patch.object(bridge.subprocess, "run") as run:
            with self.assertRaises(bridge.TmuxError):
                client.resume_agent(REPO_ROOT, "codex", "$(touch /tmp/unsafe)")

        run.assert_not_called()

    def test_resume_session_name_never_reuses_an_existing_tmux_session(self) -> None:
        client = bridge.TmuxClient(REPO_ROOT)
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"

        with mock.patch.object(
            client, "_session_exists", side_effect=[True, False]
        ) as session_exists:
            name = client._resume_session_name(REPO_ROOT, conversation_id)

        base = f"{REPO_ROOT.name}-019fba20"
        self.assertEqual(f"{base}-1", name)
        self.assertEqual(
            [mock.call(base), mock.call(f"{base}-1")],
            session_exists.call_args_list,
        )

    def test_split_utf16_preserves_complete_unicode_text(self) -> None:
        original = "a🙂b🙂c"

        chunks = bridge.split_utf16(original, 3)

        self.assertEqual(original, "".join(chunks))
        self.assertTrue(
            all(len(chunk.encode("utf-16-le")) // 2 <= 3 for chunk in chunks)
        )


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
        self.prompts = {pane.topic_key: [] for pane in panes}
        self.sent: list[tuple[str, str]] = []
        self.launches: list[tuple[Path, str]] = []
        self.resumes: list[tuple[Path, str, str]] = []
        self.launched_panes: set[str] = set()
        self.directories: dict[str, Path] = {}

    def agents(self):
        return list(self.panes)

    def message_version(self, pane):
        return self.versions.get(pane.topic_key, "")

    def last_output(self, pane):
        return self.outputs[pane.topic_key]

    def queue_prompt(self, pane, text, name="prompt"):
        prompt = bridge.CachedPrompt(Path(name), text)
        self.prompts.setdefault(pane.topic_key, []).append(prompt)
        return prompt

    def pending_local_prompts(self, pane):
        return list(self.prompts.get(pane.topic_key, []))

    def acknowledge_local_prompt(self, prompt):
        for prompts in self.prompts.values():
            if prompt in prompts:
                prompts.remove(prompt)
                return

    def send_message(self, pane, text):
        self.sent.append((pane.pane_id, text))

    def available_agents(self):
        return ["codex", "claude"]

    def launch_agent(self, repository, agent):
        self.launches.append((Path(repository), agent))
        self.launched_panes.add("%50")
        return bridge.LaunchTarget("%50", repository.name, "1")

    def resume_agent(self, repository, agent, conversation_id):
        self.resumes.append((Path(repository), agent, conversation_id))
        self.launched_panes.add("%60")
        return bridge.LaunchTarget(
            "%60", f"{repository.name}-{conversation_id[:8]}", "0"
        )

    def pane_directory(self, pane):
        directory = self.directories.get(pane.pane_id)
        if directory is None:
            raise bridge.TmuxError("test pane has no directory")
        return directory

    def pane_exists(self, pane_id):
        return pane_id in self.launched_panes or any(
            pane.pane_id == pane_id for pane in self.panes
        )


class FakeSessionMetadata:
    def __init__(self) -> None:
        self.directories: dict[tuple[str, str], Path] = {}

    def resolve(self, agent, conversation_id):
        return self.directories.get((agent, conversation_id))


class ForumTopicBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pane = bridge.AgentPane(
            "%2",
            "tr",
            "0",
            "codex",
            "1",
            "codex",
            "working",
            "codex-session-123",
        )
        self.telegram = FakeTelegram()
        self.tmux = FakeTmux([self.pane])
        self.session_metadata = FakeSessionMetadata()
        self.repository_root = Path(self.temporary.name) / "repositories"
        self.repository = self.repository_root / "MuxPager"
        (self.repository / ".git").mkdir(parents=True)
        (self.repository_root / "another-project" / ".git").mkdir(parents=True)
        self.tmux.directories[self.pane.pane_id] = self.repository
        self.instance = bridge.Bridge(
            self.telegram,
            self.tmux,
            allowed_user_id=42,
            state_path=Path(self.temporary.name) / "state.json",
            close_after=0,
            repository_root=self.repository_root,
            session_metadata=self.session_metadata,
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

    def launch_callback(self, request, data, user_id=42):
        return {
            "id": "launch-callback",
            "from": {"id": user_id},
            "data": data,
            "message": {
                "message_id": request["message_id"],
                "message_thread_id": request["thread_id"],
                "chat": {"id": -100123, "type": "supergroup"},
            },
        }

    def start_launch(self, query="mux"):
        self.configure()
        self.telegram.calls.clear()
        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 20,
                "text": f"/new {query}".rstrip(),
            }
        )
        request_id, request = next(
            iter(self.instance.state["launch_requests"].items())
        )
        return request_id, request

    def add_closed_conversation(
        self,
        conversation_id="019fba20-0898-7383-b7df-a686a55fae84",
        *,
        agent="codex",
        thread_id=77,
        working_directory=None,
        with_metadata=True,
    ):
        topic_key = "\x1f".join(("conversation", agent, conversation_id))
        record = {
            "thread_id": thread_id,
            "name": f"MuxPager · {agent} #0",
            "label": f"MuxPager › {agent} › {agent} #0",
            "closed": True,
            "conversation_id": conversation_id,
            "missing_since": 1000.0 + thread_id,
        }
        if working_directory is not None:
            record["working_directory"] = str(working_directory)
        self.instance.state.setdefault("topics", {})[topic_key] = record
        if with_metadata:
            self.session_metadata.directories[(agent, conversation_id)] = (
                Path(working_directory)
                if working_directory is not None
                else self.repository
            )
        return topic_key, record

    def start_resume(self, query=""):
        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 30,
                "text": f"/resume {query}".rstrip(),
            }
        )
        return next(
            (
                (request_id, request)
                for request_id, request in self.instance.state[
                    "launch_requests"
                ].items()
                if request.get("kind") == "resume"
            )
        )

    @staticmethod
    def resume_callback(request, data, user_id=42):
        return {
            "id": "resume-callback",
            "from": {"id": user_id},
            "data": data,
            "message": {
                "message_id": request["message_id"],
                "message_thread_id": request["thread_id"],
                "chat": {"id": -100123, "type": "supergroup"},
            },
        }

    def add_legacy_topic(
        self,
        *,
        thread_id=77,
        agent="codex",
        closed=True,
        pane_index="1",
    ):
        pane = bridge.AgentPane(
            f"%{thread_id}",
            f"legacy-{thread_id}",
            "0",
            agent,
            pane_index,
            agent,
            "done",
        )
        self.instance.state.setdefault("topics", {})[pane.legacy_topic_key] = {
            "thread_id": thread_id,
            "name": f"legacy {agent}",
            "pane_id": pane.pane_id,
            "label": pane.label,
            "closed": closed,
        }
        return pane.legacy_topic_key

    def start_legacy_prune(self):
        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 30,
                "text": "/prune legacy",
            }
        )
        return next(iter(self.instance.state["legacy_prune_requests"].items()))

    @staticmethod
    def legacy_prune_callback(request_id, request, action="y", user_id=42):
        return {
            "id": "prune-callback",
            "from": {"id": user_id},
            "data": f"pl:{action}:{request_id}",
            "message": {
                "message_id": request["message_id"],
                "message_thread_id": request["thread_id"],
                "chat": {"id": -100123, "type": "supergroup"},
            },
        }

    def test_new_command_searches_repositories_and_shows_picker(self) -> None:
        request_id, request = self.start_launch()

        self.assertEqual("choosing_repo", request["state"])
        picker = next(
            params
            for method, params in self.telegram.calls
            if method == "sendMessage" and "reply_markup" in params
        )
        self.assertIn("matching “mux”", picker["text"])
        button = picker["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual("MuxPager", button["text"])
        self.assertEqual(f"nl:r:{request_id}:0", button["callback_data"])

    def test_repository_and_agent_buttons_launch_new_conversation(self) -> None:
        request_id, request = self.start_launch()
        self.instance._handle_callback_query(
            self.launch_callback(request, f"nl:r:{request_id}:0")
        )
        request = self.instance.state["launch_requests"][request_id]
        self.assertEqual("choosing_agent", request["state"])
        agent_picker = [
            params
            for method, params in self.telegram.calls
            if method == "editMessageText" and "Start a new conversation" in params["text"]
        ][-1]
        self.assertEqual(
            ["Codex", "Claude", "Cancel"],
            [row[0]["text"] for row in agent_picker["reply_markup"]["inline_keyboard"]],
        )

        self.instance._handle_callback_query(
            self.launch_callback(request, f"nl:a:{request_id}:codex")
        )

        launched = self.instance.state["launch_requests"][request_id]
        self.assertEqual("starting", launched["state"])
        self.assertEqual([(self.repository.resolve(), "codex")], self.tmux.launches)
        self.assertEqual("MuxPager:1", launched["tmux_target"])

    def test_launch_becomes_ready_only_after_conversation_topic_exists(self) -> None:
        request_id, request = self.start_launch()
        self.instance._handle_callback_query(
            self.launch_callback(request, f"nl:r:{request_id}:0")
        )
        request = self.instance.state["launch_requests"][request_id]
        self.instance._handle_callback_query(
            self.launch_callback(request, f"nl:a:{request_id}:codex")
        )
        self.tmux.panes.append(
            bridge.AgentPane(
                "%50", "MuxPager", "1", "codex", "0", "codex", "working",
                "new-conversation-123",
            )
        )

        self.instance._sync_topics()

        ready = self.instance.state["launch_requests"][request_id]
        self.assertEqual("ready", ready["state"])
        self.assertEqual("new-conversation-123", ready["conversation_id"])
        confirmation = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "editMessageText" and "is ready" in params["text"]
        ][-1]
        self.assertIn("MuxPager · codex #0", confirmation)

    def test_unauthorized_launch_button_cannot_start_agent(self) -> None:
        request_id, request = self.start_launch()

        self.instance._handle_callback_query(
            self.launch_callback(request, f"nl:r:{request_id}:0", user_id=999)
        )

        self.assertEqual("choosing_repo", request["state"])
        self.assertEqual([], self.tmux.launches)

    def test_expired_launch_button_cannot_select_repository(self) -> None:
        request_id, request = self.start_launch()
        request["created_at"] = (
            bridge.time.time() - bridge.LAUNCH_REQUEST_TTL - 1
        )

        self.instance._handle_callback_query(
            self.launch_callback(request, f"nl:r:{request_id}:0")
        )

        self.assertEqual("choosing_repo", request["state"])
        self.assertEqual([], self.tmux.launches)
        answer = [
            params
            for method, params in self.telegram.calls
            if method == "answerCallbackQuery"
        ][-1]
        self.assertTrue(answer["show_alert"])
        self.assertIn("expired", answer["text"])

    def test_new_command_is_rejected_inside_an_agent_topic(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()

        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 21,
                "message_thread_id": thread_id,
                "text": "/new mux",
            }
        )

        self.assertEqual({}, self.instance.state.get("launch_requests"))
        reply = next(
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        )
        self.assertIn("Control topic", reply)

    def test_resume_lists_only_resolvable_closed_conversations(self) -> None:
        self.configure()
        topic_key, record = self.add_closed_conversation()
        self.add_closed_conversation(
            "b57bf8e4-59a7-4b10-a809-fa8ef9b3acee",
            agent="claude",
            thread_id=78,
            with_metadata=False,
        )
        open_id = "019fbe91-30b4-72a3-8924-ef219a769a01"
        open_key, open_record = self.add_closed_conversation(
            open_id, thread_id=79
        )
        open_record["closed"] = False
        self.telegram.calls.clear()

        request_id, request = self.start_resume("mux")

        self.assertEqual("choosing_resume", request["state"])
        self.assertEqual([topic_key], [item["topic_key"] for item in request["candidates"]])
        self.assertEqual(
            str(self.repository.resolve()), record["working_directory"]
        )
        self.assertNotIn(open_key, [item["topic_key"] for item in request["candidates"]])
        picker = next(
            params
            for method, params in self.telegram.calls
            if method == "sendMessage" and "reply_markup" in params
        )
        self.assertIn("1 other closed conversation", picker["text"])
        self.assertEqual(
            f"rs:r:{request_id}:0",
            picker["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
        )

    def test_resume_selection_starts_exact_conversation_in_dedicated_session(
        self,
    ) -> None:
        self.configure()
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        self.add_closed_conversation(conversation_id)
        request_id, request = self.start_resume()
        self.telegram.calls.clear()

        self.instance._handle_callback_query(
            self.resume_callback(request, f"rs:r:{request_id}:0")
        )

        self.assertEqual(
            [(self.repository.resolve(), "codex", conversation_id)],
            self.tmux.resumes,
        )
        self.assertEqual("starting", request["state"])
        self.assertEqual("MuxPager-019fba20", request["session"])
        status = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "editMessageText"
        ][-1]
        self.assertIn("Dedicated tmux session", status)

    def test_resumed_conversation_reopens_original_topic_when_identity_matches(
        self,
    ) -> None:
        self.configure()
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        topic_key, _ = self.add_closed_conversation(conversation_id)
        request_id, request = self.start_resume()
        self.instance._handle_callback_query(
            self.resume_callback(request, f"rs:r:{request_id}:0")
        )
        resumed = bridge.AgentPane(
            "%60",
            request["session"],
            "0",
            "codex",
            "0",
            "codex",
            "working",
            conversation_id,
        )
        self.tmux.panes.append(resumed)
        self.tmux.directories[resumed.pane_id] = self.repository
        self.telegram.calls.clear()

        self.instance._sync_topics()

        self.assertFalse(self.instance.state["topics"][topic_key]["closed"])
        self.assertIn(
            (
                "reopenForumTopic",
                {"chat_id": -100123, "message_thread_id": 77},
            ),
            self.telegram.calls,
        )
        self.assertEqual("ready", request["state"])
        ready = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "editMessageText"
        ][-1]
        self.assertIn("Telegram topic reopened", ready)
        self.assertIn("Dedicated tmux session", ready)

    def test_resume_fails_if_cli_reports_a_different_conversation(self) -> None:
        self.configure()
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        self.add_closed_conversation(conversation_id)
        request_id, request = self.start_resume()
        self.instance._handle_callback_query(
            self.resume_callback(request, f"rs:r:{request_id}:0")
        )
        wrong = bridge.AgentPane(
            "%60",
            request["session"],
            "0",
            "codex",
            "0",
            "codex",
            "working",
            "019fbe91-30b4-72a3-8924-ef219a769a01",
        )
        self.tmux.panes.append(wrong)
        self.tmux.directories[wrong.pane_id] = self.repository

        self.instance._sync_topics()

        self.assertEqual("failed", request["state"])
        failure = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "editMessageText"
        ][-1]
        self.assertIn("started a different conversation", failure)

    def test_resume_refuses_to_duplicate_a_conversation_that_became_live(self) -> None:
        self.configure()
        conversation_id = "019fba20-0898-7383-b7df-a686a55fae84"
        self.add_closed_conversation(conversation_id)
        request_id, request = self.start_resume()
        self.tmux.panes.append(
            bridge.AgentPane(
                "%70", "existing", "0", "codex", "0", "codex", "working",
                conversation_id,
            )
        )
        self.telegram.calls.clear()

        self.instance._handle_callback_query(
            self.resume_callback(request, f"rs:r:{request_id}:0")
        )

        self.assertEqual([], self.tmux.resumes)
        self.assertEqual("cancelled", request["state"])
        result = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "editMessageText"
        ][-1]
        self.assertIn("already active", result)

    def test_resume_callback_is_bound_to_authorized_picker_message(self) -> None:
        self.configure()
        self.add_closed_conversation()
        request_id, request = self.start_resume()
        self.telegram.calls.clear()

        self.instance._handle_callback_query(
            self.resume_callback(request, f"rs:r:{request_id}:0", user_id=999)
        )
        forged = self.resume_callback(request, f"rs:r:{request_id}:0")
        forged["message"]["message_id"] += 1
        self.instance._handle_callback_query(forged)

        self.assertEqual([], self.tmux.resumes)
        self.assertEqual("choosing_resume", request["state"])

    def test_resume_command_is_rejected_inside_an_agent_topic(self) -> None:
        thread_id = self.configure()
        self.add_closed_conversation()
        self.telegram.calls.clear()

        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 31,
                "message_thread_id": thread_id,
                "text": "/resume muxpager",
            }
        )

        self.assertFalse(
            any(
                request.get("kind") == "resume"
                for request in self.instance.state.get("launch_requests", {}).values()
            )
        )
        reply = next(
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        )
        self.assertIn("Control topic", reply)

    def test_prune_legacy_previews_only_closed_pre_upgrade_topics(self) -> None:
        self.configure()
        closed_legacy = self.add_legacy_topic(thread_id=77)
        self.add_legacy_topic(thread_id=78, closed=False)
        self.add_legacy_topic(thread_id=79, agent="devin")
        modern = bridge.AgentPane(
            "%80", "modern", "0", "codex", "0", "codex", "done", "session-80"
        )
        self.instance.state["topics"][modern.topic_key] = {
            "thread_id": 80,
            "closed": True,
        }
        self.telegram.calls.clear()

        request_id, request = self.start_legacy_prune()

        self.assertEqual([closed_legacy], request["topic_keys"])
        self.assertEqual("pending", request["state"])
        self.assertFalse(
            any(method == "deleteForumTopic" for method, _ in self.telegram.calls)
        )
        confirmation = next(
            params
            for method, params in self.telegram.calls
            if method == "sendMessage" and "reply_markup" in params
        )
        self.assertIn("Delete 1 closed legacy topic?", confirmation["text"])
        self.assertEqual(
            f"pl:y:{request_id}",
            confirmation["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
        )

    def test_confirmed_legacy_prune_deletes_topic_and_local_mapping(self) -> None:
        self.configure()
        legacy_key = self.add_legacy_topic(thread_id=77)
        self.instance.state["agent_status"] = {legacy_key: "done"}
        self.instance.state["message_versions"] = {legacy_key: "v1"}
        request_id, request = self.start_legacy_prune()
        self.telegram.calls.clear()

        self.instance._handle_callback_query(
            self.legacy_prune_callback(request_id, request)
        )

        self.assertIn(
            (
                "deleteForumTopic",
                {"chat_id": -100123, "message_thread_id": 77},
            ),
            self.telegram.calls,
        )
        self.assertNotIn(legacy_key, self.instance.state["topics"])
        self.assertNotIn(legacy_key, self.instance.state["agent_status"])
        self.assertNotIn(legacy_key, self.instance.state["message_versions"])
        self.assertEqual("completed", request["state"])
        self.assertEqual(1, request["deleted"])

    def test_legacy_prune_cleans_missing_topics_but_retains_api_failures(self) -> None:
        self.configure()
        missing_key = self.add_legacy_topic(thread_id=77)
        failed_key = self.add_legacy_topic(thread_id=78)
        request_id, request = self.start_legacy_prune()
        self.telegram.calls.clear()
        real_call = self.telegram.call

        def delete_with_failures(method, params=None, http_timeout=30):
            if method == "deleteForumTopic":
                self.telegram.calls.append((method, dict(params or {})))
                if params["message_thread_id"] == 77:
                    raise bridge.TelegramError("Bad Request: message thread not found")
                raise bridge.TelegramError("Forbidden: not enough rights")
            return real_call(method, params, http_timeout)

        with mock.patch.object(self.telegram, "call", side_effect=delete_with_failures):
            self.instance._handle_callback_query(
                self.legacy_prune_callback(request_id, request)
            )

        self.assertNotIn(missing_key, self.instance.state["topics"])
        self.assertIn(failed_key, self.instance.state["topics"])
        self.assertEqual(1, request["deleted"])
        self.assertEqual(1, request["failed"])
        result = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "editMessageText"
        ][-1]
        self.assertIn("run /prune legacy to retry", result)

    def test_legacy_prune_revalidates_live_topics_before_deleting(self) -> None:
        self.configure()
        legacy_key = self.add_legacy_topic(thread_id=77)
        request_id, request = self.start_legacy_prune()
        self.telegram.calls.clear()
        parts = legacy_key.split("\x1f")
        self.tmux.panes = [
            bridge.AgentPane(
                "%77", parts[0], parts[1], "codex", parts[2], parts[3], "working"
            )
        ]

        self.instance._handle_callback_query(
            self.legacy_prune_callback(request_id, request)
        )

        self.assertIn(legacy_key, self.instance.state["topics"])
        self.assertFalse(
            any(method == "deleteForumTopic" for method, _ in self.telegram.calls)
        )
        self.assertEqual(1, request["skipped"])

    def test_legacy_prune_requires_bound_authorized_confirmation(self) -> None:
        self.configure()
        legacy_key = self.add_legacy_topic(thread_id=77)
        request_id, request = self.start_legacy_prune()
        self.telegram.calls.clear()

        self.instance._handle_callback_query(
            self.legacy_prune_callback(request_id, request, user_id=999)
        )
        forged = self.legacy_prune_callback(request_id, request)
        forged["message"]["message_id"] += 1
        self.instance._handle_callback_query(forged)

        self.assertIn(legacy_key, self.instance.state["topics"])
        self.assertEqual("pending", request["state"])
        self.assertFalse(
            any(method == "deleteForumTopic" for method, _ in self.telegram.calls)
        )

    def test_legacy_prune_can_be_cancelled(self) -> None:
        self.configure()
        legacy_key = self.add_legacy_topic(thread_id=77)
        request_id, request = self.start_legacy_prune()
        self.telegram.calls.clear()

        self.instance._handle_callback_query(
            self.legacy_prune_callback(request_id, request, action="x")
        )

        self.assertIn(legacy_key, self.instance.state["topics"])
        self.assertEqual("cancelled", request["state"])
        self.assertFalse(
            any(method == "deleteForumTopic" for method, _ in self.telegram.calls)
        )

    def test_prune_legacy_is_rejected_inside_an_agent_topic(self) -> None:
        thread_id = self.configure()
        self.add_legacy_topic(thread_id=77)
        self.telegram.calls.clear()

        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 31,
                "message_thread_id": thread_id,
                "text": "/prune legacy",
            }
        )

        self.assertEqual({}, self.instance.state.get("legacy_prune_requests"))
        reply = next(
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        )
        self.assertIn("Control topic", reply)

    def create_choice(self):
        return self.instance.choice_store.create(
            pane_id=self.pane.pane_id,
            session=self.pane.session,
            window_index=self.pane.window_index,
            pane_index=self.pane.pane_index,
            agent=self.pane.agent,
            conversation_id=self.pane.conversation_id,
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
        self.assertEqual(
            str(self.repository.resolve()),
            self.instance.state["topics"][self.pane.topic_key]["working_directory"],
        )
        methods = [method for method, _ in self.telegram.calls]
        self.assertIn("editGeneralForumTopic", methods)
        self.assertIn("createForumTopic", methods)
        create = next(
            params
            for method, params in self.telegram.calls
            if method == "createForumTopic"
        )
        self.assertEqual("tr · codex #1", create["name"])

    def test_existing_topic_title_is_never_automatically_renamed(self) -> None:
        thread_id = self.configure()
        original_name = self.instance.state["topics"][self.pane.topic_key]["name"]
        self.telegram.calls.clear()
        moved = bridge.AgentPane(
            "%99",
            "other-session",
            "3",
            "new-window-title",
            "0",
            "codex",
            "working",
            self.pane.conversation_id,
        )
        self.tmux.panes = [moved]

        self.instance._sync_topics()

        record = self.instance.state["topics"][moved.topic_key]
        self.assertEqual(thread_id, record["thread_id"])
        self.assertEqual(original_name, record["name"])
        self.assertEqual("%99", record["pane_id"])
        self.assertNotIn(
            "editForumTopic", [method for method, _ in self.telegram.calls]
        )

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

    def test_local_prompt_is_mirrored_silently_into_its_topic(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        self.tmux.queue_prompt(self.pane, "Please run the tests\nand report back.")

        self.instance._sync_topics()

        messages = [
            params
            for method, params in self.telegram.calls
            if method == "sendMessage"
            and params.get("message_thread_id") == thread_id
            and params["text"].startswith("💻 You (tmux):")
        ]
        self.assertEqual(1, len(messages))
        self.assertEqual(
            "💻 You (tmux):\n\nPlease run the tests\nand report back.",
            messages[0]["text"],
        )
        self.assertTrue(messages[0]["disable_notification"])
        self.assertEqual([], self.tmux.pending_local_prompts(self.pane))

    def test_long_local_prompt_is_split_without_losing_text(self) -> None:
        self.configure()
        self.telegram.calls.clear()
        prompt = ("🙂 local context\n" * 500) + "final instruction"
        self.tmux.queue_prompt(self.pane, prompt)

        self.instance._sync_topics()

        messages = [
            params
            for method, params in self.telegram.calls
            if method == "sendMessage"
            and params["text"].startswith("💻 You (tmux) [")
        ]
        self.assertGreater(len(messages), 1)
        mirrored = "".join(
            message["text"].split("\n\n", 1)[1] for message in messages
        )
        self.assertEqual(prompt, mirrored)
        self.assertTrue(
            all(
                len(message["text"].encode("utf-16-le")) // 2
                <= bridge.TELEGRAM_TEXT_LIMIT
                for message in messages
            )
        )
        self.assertEqual([], self.tmux.pending_local_prompts(self.pane))

    def test_completed_reply_is_posted_as_a_fresh_topic_message(self) -> None:
        thread_id = self.configure()
        self.telegram.calls.clear()
        done = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "done", "codex-session-123"
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
            "%99",
            "tr",
            "0",
            "codex",
            "1",
            "codex",
            "working",
            "codex-session-123",
        )
        self.tmux.panes = [replacement]
        moved_directory = self.repository_root / "another-project"
        self.tmux.directories[replacement.pane_id] = moved_directory

        self.instance._sync_topics()

        record = self.instance.state["topics"][replacement.topic_key]
        self.assertEqual(thread_id, record["thread_id"])
        self.assertEqual("%99", record["pane_id"])
        self.assertEqual(
            str(moved_directory.resolve()), record["working_directory"]
        )
        self.assertNotIn(
            "createForumTopic", [method for method, _ in self.telegram.calls]
        )

    def test_new_conversation_in_the_same_tmux_slot_gets_a_new_topic(self) -> None:
        first_thread_id = self.configure()
        self.telegram.calls.clear()
        replacement = bridge.AgentPane(
            "%2",
            "tr",
            "0",
            "codex",
            "1",
            "codex",
            "working",
            "codex-session-999",
        )
        self.tmux.panes = [replacement]

        self.instance._sync_topics()

        replacement_record = self.instance.state["topics"][replacement.topic_key]
        self.assertNotEqual(first_thread_id, replacement_record["thread_id"])
        self.assertTrue(self.instance.state["topics"][self.pane.topic_key]["closed"])
        self.assertEqual(
            1,
            sum(
                method == "createForumTopic" for method, _ in self.telegram.calls
            ),
        )

    def test_open_legacy_topic_is_migrated_to_conversation_identity(self) -> None:
        thread_id = self.configure()
        record = self.instance.state["topics"].pop(self.pane.topic_key)
        self.instance.state["topics"][self.pane.legacy_topic_key] = record
        self.instance.state["agent_status"] = {
            self.pane.legacy_topic_key: "working"
        }
        self.instance._save_state()
        self.telegram.calls.clear()

        self.instance._sync_topics()

        self.assertNotIn(self.pane.legacy_topic_key, self.instance.state["topics"])
        migrated = self.instance.state["topics"][self.pane.topic_key]
        self.assertEqual(thread_id, migrated["thread_id"])
        self.assertEqual(self.pane.conversation_id, migrated["conversation_id"])
        self.assertNotIn(
            "createForumTopic", [method for method, _ in self.telegram.calls]
        )

    def test_missing_conversation_identity_protects_legacy_topic_and_pauses_delivery(
        self,
    ) -> None:
        thread_id = self.configure()
        record = self.instance.state["topics"].pop(self.pane.topic_key)
        self.instance.state["topics"][self.pane.legacy_topic_key] = record
        pending = bridge.AgentPane(
            "%2", "tr", "0", "codex", "1", "codex", "working"
        )
        self.tmux.panes = [pending]
        self.telegram.calls.clear()

        count = self.instance._sync_topics()

        self.assertEqual(0, count)
        self.assertFalse(
            self.instance.state["topics"][pending.legacy_topic_key]["closed"]
        )
        self.assertNotIn(
            "createForumTopic", [method for method, _ in self.telegram.calls]
        )

        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 12,
                "message_thread_id": thread_id,
                "text": "Do not guess the target",
            }
        )

        self.assertEqual([], self.tmux.sent)
        warning = [
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        ][-1]
        self.assertIn("has not reported its conversation identity", warning)

    def test_identity_pending_preserves_a_legacy_choice_for_migration(self) -> None:
        self.configure()
        choice = self.create_choice()
        self.instance.choice_store.update(
            choice["id"],
            lambda current: {
                key: value
                for key, value in {
                    **current,
                    "topic_key": self.pane.legacy_topic_key,
                }.items()
                if key != "conversation_id"
            },
        )
        self.tmux.panes = [
            bridge.AgentPane(
                "%2", "tr", "0", "codex", "1", "codex", "done"
            )
        ]

        self.instance._sync_topics()

        preserved = self.instance.choice_store.get(choice["id"])
        self.assertEqual("pending", preserved["state"])
        self.assertEqual(self.pane.legacy_topic_key, preserved["topic_key"])

    def test_send_rejects_if_conversation_moves_during_final_revalidation(
        self,
    ) -> None:
        client = bridge.TmuxClient(REPO_ROOT)
        moved = bridge.AgentPane(
            "%99",
            self.pane.session,
            self.pane.window_index,
            self.pane.window_name,
            self.pane.pane_index,
            self.pane.agent,
            self.pane.status,
            self.pane.conversation_id,
        )
        result = subprocess.CompletedProcess(
            ["tmux"], 0, stdout="0\t%2\n", stderr=""
        )
        with mock.patch.object(bridge.subprocess, "run", return_value=result):
            with mock.patch.object(client, "agents", return_value=[moved]):
                with self.assertRaises(bridge.TmuxError):
                    client.send_message(self.pane, "Do not send after a move")

    def test_duplicate_live_conversation_pauses_message_routing(self) -> None:
        thread_id = self.configure()
        duplicate = bridge.AgentPane(
            "%99",
            "other",
            "2",
            "claude",
            "0",
            "codex",
            "done",
            self.pane.conversation_id,
        )
        self.tmux.panes = [self.pane, duplicate]
        self.telegram.calls.clear()

        self.instance._handle_message(
            {
                **self.control_message,
                "message_id": 13,
                "message_thread_id": thread_id,
                "text": "Do not send ambiguously",
            }
        )

        self.assertEqual([], self.tmux.sent)
        warning = next(
            params["text"]
            for method, params in self.telegram.calls
            if method == "sendMessage"
        )
        self.assertIn("open in multiple tmux panes", warning)

    def test_duplicate_live_conversation_preserves_choice_without_delivering(
        self,
    ) -> None:
        thread_id = self.configure()
        choice = self.create_choice()
        self.instance._sync_topics()
        published = self.instance.choice_store.get(choice["id"])
        duplicate = bridge.AgentPane(
            "%99",
            "other",
            "2",
            "codex",
            "0",
            "codex",
            "done",
            self.pane.conversation_id,
        )
        self.tmux.panes = [
            bridge.AgentPane(
                "%2",
                "tr",
                "0",
                "codex",
                "1",
                "codex",
                "done",
                self.pane.conversation_id,
            ),
            duplicate,
        ]

        self.instance._handle_callback_query(self.callback(published, thread_id))

        self.assertEqual([], self.tmux.sent)
        self.assertEqual(
            "selected", self.instance.choice_store.get(choice["id"])["state"]
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
                "%2", "tr", "0", "codex", "1", "codex", "done", "codex-session-123"
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
                "%2", "tr", "0", "codex", "1", "codex", "done", "codex-session-123"
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
                "%2", "tr", "0", "codex", "1", "codex", "done", "codex-session-123"
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
            "%2", "tr", "0", "codex", "1", "codex", "done", "codex-session-123"
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

    def test_active_choice_follows_its_conversation_to_a_new_pane(self) -> None:
        self.configure()
        choice = self.create_choice()
        replacement = bridge.AgentPane(
            "%99",
            "other",
            "2",
            "codex",
            "0",
            "codex",
            "working",
            self.pane.conversation_id,
        )
        self.tmux.panes = [replacement]

        self.instance._sync_topics()

        moved = self.instance.choice_store.get(choice["id"])
        self.assertEqual("published", moved["state"])
        self.assertEqual("%99", moved["pane_id"])
        self.assertEqual("other", moved["session"])
        self.assertEqual(replacement.topic_key, moved["topic_key"])

    def test_legacy_choice_is_migrated_to_its_live_conversation(self) -> None:
        self.configure()
        choice = self.create_choice()
        self.instance.choice_store.update(
            choice["id"],
            lambda current: {
                key: value
                for key, value in {
                    **current,
                    "topic_key": self.pane.legacy_topic_key,
                }.items()
                if key != "conversation_id"
            },
        )

        self.instance._sync_topics()

        migrated = self.instance.choice_store.get(choice["id"])
        self.assertEqual("published", migrated["state"])
        self.assertEqual(self.pane.topic_key, migrated["topic_key"])
        self.assertEqual(self.pane.conversation_id, migrated["conversation_id"])


if __name__ == "__main__":
    unittest.main()
