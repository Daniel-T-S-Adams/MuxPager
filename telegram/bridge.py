#!/usr/bin/env python3
"""Telegram forum topics for live Codex and Claude panes in MuxPager."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


LOG = logging.getLogger("tmux-telegram-bridge")
TELEGRAM_TEXT_LIMIT = 4096
MAX_INPUT_LENGTH = 4096
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class TelegramError(RuntimeError):
    """A Bot API error whose message never contains the bot token."""


class TmuxError(RuntimeError):
    """A tmux operation could not be completed safely."""


@dataclass(frozen=True)
class AgentPane:
    pane_id: str
    session: str
    window_index: str
    window_name: str
    pane_index: str
    agent: str
    status: str

    @property
    def label(self) -> str:
        window = self.window_name or f"window {self.window_index}"
        return f"{self.session} › {window} › {self.agent} #{self.pane_index}"

    @property
    def topic_key(self) -> str:
        # Pane IDs are reused when a tmux server restarts. Session/window/pane
        # position plus agent type is stable enough to reopen the existing
        # Telegram topic while still separating two agents in one window.
        return "\x1f".join(
            (self.session, self.window_index, self.pane_index, self.agent)
        )

    @property
    def topic_name(self) -> str:
        parts = [self.session]
        window = self.window_name.strip()
        if window and window.casefold() != self.agent.casefold():
            parts.append(window)
        parts.append(f"{self.agent} #{self.pane_index}")
        return trim_label(" · ".join(parts), 128)


def parse_agent_rows(output: str) -> list[AgentPane]:
    panes: list[AgentPane] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 7 or not fields[0].startswith("%"):
            continue
        pane_id, session, window_index, window_name, pane_index, agent, status = fields
        if agent not in {"claude", "codex", "devin"}:
            continue
        if status not in {"working", "done", "ask", "wait", "parked"}:
            status = "done"
        panes.append(
            AgentPane(
                pane_id=pane_id,
                session=session,
                window_index=window_index,
                window_name=window_name,
                pane_index=pane_index,
                agent=agent,
                status=status,
            )
        )
    return panes


def status_icon(status: str) -> str:
    return {
        "working": "🟡",
        "done": "❓",
        "ask": "❓",
        "wait": "⏸",
        "parked": "📌",
    }.get(status, "❓")


def status_text(status: str) -> str:
    return {
        "working": "working",
        "done": "waiting for you",
        "ask": "asking for input",
        "wait": "snoozed",
        "parked": "parked",
    }.get(status, status)


def trim_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 20:
        return text[:limit]
    marker = "\n… output shortened …\n"
    remaining = limit - len(marker)
    head = min(800, remaining // 3)
    return text[:head] + marker + text[-(remaining - head) :]


def trim_label(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 2:
        return text[:limit]
    return text[: limit - 1] + "…"


def clean_capture(output: str) -> str:
    output = ANSI_RE.sub("", output).replace("\x00", "")
    lines = [line.rstrip() for line in output.splitlines()]

    # Codex and Claude keep the current input prompt and model/status footer at
    # the bottom of the pane. They are terminal chrome, not the agent message.
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].lstrip()
        if stripped.startswith(("› ", "❯ ")):
            lines = lines[:index]
            break

    footer_re = re.compile(
        r"^(?:[─━](?:.*\bWorked for\b|[─━]{3,})|(?:•\s+)?Working\s*\(|gpt-[\w.-]+\s|claude(?:\s|$))",
        re.IGNORECASE,
    )
    while lines and (not lines[-1].strip() or footer_re.match(lines[-1].strip())):
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


class TmuxClient:
    def __init__(self, repo_root: Path, tmux_bin: str = "tmux") -> None:
        self.repo_root = repo_root
        self.tmux_bin = tmux_bin
        self.list_script = repo_root / "bin/muxpager-agent-list"
        status_root = os.environ.get(
            "TMUX_AGENT_STATUS_DIR", str(Path.home() / ".cache/tmux-agent-status")
        )
        self.message_dir = Path(status_root) / "panes"

    def agents(self) -> list[AgentPane]:
        try:
            result = subprocess.run(
                ["bash", str(self.list_script)],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TmuxError(f"Could not list tmux panes: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or "agent pane listing failed"
            raise TmuxError(detail)
        return parse_agent_rows(result.stdout)

    def _message_path(self, pane: AgentPane) -> Path:
        return self.message_dir / f"{pane.session}_{pane.pane_id}.last-message"

    def message_version(self, pane: AgentPane) -> str:
        try:
            stat = self._message_path(pane).stat()
        except OSError:
            return ""
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def capture(self, pane_id: str) -> str:
        try:
            result = subprocess.run(
                [
                    self.tmux_bin,
                    "capture-pane",
                    "-p",
                    "-J",
                    "-t",
                    pane_id,
                    "-S",
                    "-160",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise TmuxError(f"Could not capture pane {pane_id}") from exc
        return clean_capture(result.stdout)

    def last_output(self, pane: AgentPane) -> tuple[str, str]:
        if pane.status != "working":
            try:
                cached = self._message_path(pane).read_text(encoding="utf-8").strip()
            except OSError:
                cached = ""
            if cached:
                return cached, "Last agent message"

        captured = self.capture(pane.pane_id)
        if captured:
            return captured, "Latest pane output"
        return "No visible output yet.", "Latest pane output"

    def _validate_target(self, pane_id: str) -> None:
        try:
            result = subprocess.run(
                [
                    self.tmux_bin,
                    "display-message",
                    "-p",
                    "-t",
                    pane_id,
                    "#{pane_dead}\t#{pane_id}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise TmuxError(f"Pane {pane_id} is no longer available") from exc
        if result.stdout.strip() != f"0\t{pane_id}":
            raise TmuxError(f"Pane {pane_id} is no longer a live target")

    def send_message(self, pane_id: str, message: str) -> None:
        if not message.strip():
            raise TmuxError("Refusing to send an empty message")
        if len(message) > MAX_INPUT_LENGTH:
            raise TmuxError(f"Message is longer than {MAX_INPUT_LENGTH} characters")
        self._validate_target(pane_id)

        buffer_name = f"telegram-{os.getpid()}-{time.time_ns()}"
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False, prefix="tmux-telegram-"
            ) as handle:
                handle.write(message)
                temporary_path = handle.name
            os.chmod(temporary_path, 0o600)
            subprocess.run(
                [self.tmux_bin, "load-buffer", "-b", buffer_name, temporary_path],
                check=True,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                [
                    self.tmux_bin,
                    "paste-buffer",
                    "-d",
                    "-p",
                    "-r",
                    "-b",
                    buffer_name,
                    "-t",
                    pane_id,
                ],
                check=True,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                [self.tmux_bin, "send-keys", "-t", pane_id, "Enter"],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            try:
                subprocess.run(
                    [self.tmux_bin, "delete-buffer", "-b", buffer_name],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise TmuxError(f"Could not send the message to pane {pane_id}") from exc
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(
        self, method: str, params: dict[str, Any] | None = None, http_timeout: int = 30
    ) -> Any:
        encoded: dict[str, str] = {}
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                encoded[key] = json.dumps(value, separators=(",", ":"))
            elif isinstance(value, bool):
                encoded[key] = "true" if value else "false"
            else:
                encoded[key] = str(value)

        body = parse.urlencode(encoded).encode("utf-8")
        api_request = request.Request(self.base_url + method, data=body, method="POST")
        try:
            with request.urlopen(api_request, timeout=http_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                description = payload.get("description", f"HTTP {exc.code}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                description = f"HTTP {exc.code}"
            raise TelegramError(f"Telegram {method} failed: {description}") from None
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            reason = getattr(exc, "reason", exc.__class__.__name__)
            raise TelegramError(f"Telegram {method} failed: {reason}") from None

        if not payload.get("ok"):
            raise TelegramError(
                f"Telegram {method} failed: {payload.get('description', 'unknown error')}"
            )
        return payload.get("result")


class Bridge:
    def __init__(
        self,
        telegram: TelegramClient,
        tmux: TmuxClient,
        allowed_user_id: int | None,
        state_path: Path,
        poll_timeout: int = 5,
        notify: bool = True,
        close_after: int = 60,
    ) -> None:
        self.telegram = telegram
        self.tmux = tmux
        self.allowed_user_id = allowed_user_id
        self.state_path = state_path
        self.poll_timeout = max(1, min(poll_timeout, 25))
        self.notify = notify
        self.close_after = max(0, close_after)
        self.state = self._load_state()
        self._bot_id: int | None = None

    def _load_state(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.state_path.with_name(f"{self.state_path.name}.tmp.{os.getpid()}")
        temporary.write_text(json.dumps(self.state, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)

    def _is_authorized(self, user_id: Any) -> bool:
        return self.allowed_user_id is not None and user_id == self.allowed_user_id

    def _group_chat_id(self) -> int | None:
        chat_id = self.state.get("group_chat_id")
        return chat_id if isinstance(chat_id, int) else None

    def _topics(self) -> dict[str, dict[str, Any]]:
        topics = self.state.get("topics")
        if not isinstance(topics, dict):
            topics = {}
            self.state["topics"] = topics
        return topics

    def _pairing_reply(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") != "private" or not chat.get("id"):
            return
        text = (
            "This bridge is in pairing mode and cannot expose tmux yet.\n\n"
            f"Your Telegram user ID: {sender.get('id')}\n"
            "\nSet TMUX_TELEGRAM_ALLOWED_USER_ID to that user ID, then restart "
            "the service. After that, create the private Topics group and send "
            "/setup in its General topic."
        )
        self.telegram.call("sendMessage", {"chat_id": chat["id"], "text": text})

    def _send_message(
        self,
        chat_id: int,
        text: str,
        thread_id: int | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        text = trim_middle(text, TELEGRAM_TEXT_LIMIT)
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if thread_id is not None:
            params["message_thread_id"] = thread_id
        result = self.telegram.call("sendMessage", params)
        if not isinstance(result, dict):
            raise TelegramError("Telegram sendMessage returned no Message")
        return result

    def _reply(self, message: dict[str, Any], text: str) -> None:
        chat = message.get("chat") or {}
        thread_id = message.get("message_thread_id")
        self._send_message(
            int(chat["id"]),
            text,
            int(thread_id) if isinstance(thread_id, int) else None,
            disable_notification=True,
        )

    def _get_bot_id(self) -> int:
        if self._bot_id is None:
            identity = self.telegram.call("getMe")
            if not isinstance(identity, dict) or not isinstance(identity.get("id"), int):
                raise TelegramError("Telegram getMe returned no bot id")
            self._bot_id = identity["id"]
        return self._bot_id

    def _configure_group(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = int(chat["id"])
        full_chat = self.telegram.call("getChat", {"chat_id": chat_id})
        if not isinstance(full_chat, dict):
            self._reply(message, "Telegram returned no group information.")
            return
        if full_chat.get("type") != "supergroup" or not full_chat.get("is_forum"):
            self._reply(
                message,
                "This must be a supergroup with Topics enabled. Enable Topics "
                "in the group settings, then send /setup again.",
            )
            return
        if full_chat.get("username"):
            self._reply(
                message,
                "This group has a public username. Remove it first: the tmux "
                "agent group must remain private.",
            )
            return
        member_count = self.telegram.call(
            "getChatMemberCount", {"chat_id": chat_id}
        )
        if not isinstance(member_count, int) or isinstance(member_count, bool):
            self._reply(message, "Telegram returned no valid group member count.")
            return
        if member_count != 2:
            self._reply(
                message,
                "For privacy, this group must contain exactly two members: "
                "you and this bot. Remove every other member, then send /setup "
                "again.",
            )
            return

        member = self.telegram.call(
            "getChatMember", {"chat_id": chat_id, "user_id": self._get_bot_id()}
        )
        if not isinstance(member, dict) or member.get("status") not in {
            "administrator",
            "creator",
        }:
            self._reply(
                message,
                "Make the bot a group administrator with Manage Topics "
                "permission, then send /setup again.",
            )
            return
        if member.get("status") != "creator" and not member.get("can_manage_topics"):
            self._reply(
                message,
                "The bot is an administrator but does not have Manage Topics "
                "permission. Grant it and send /setup again.",
            )
            return

        previous_group = self._group_chat_id()
        if previous_group != chat_id:
            # Topic IDs are local to a group; never carry mappings across.
            self.state["topics"] = {}
            self.state.pop("agent_status", None)
            self.state.pop("message_versions", None)
            self.state["topic_sync_initialized"] = False
        self.state["group_chat_id"] = chat_id
        self.state["group_title"] = str(full_chat.get("title") or "tmux agents")
        self._save_state()

        try:
            self.telegram.call(
                "editGeneralForumTopic", {"chat_id": chat_id, "name": "Control"}
            )
        except TelegramError as exc:
            LOG.warning("Could not rename the General topic: %s", exc)

        self._reply(
            message,
            "✓ Private tmux agent group connected.\n\n"
            "Creating one topic per live agent pane. Use /sync here whenever "
            "you want to force an immediate reconciliation.",
        )
        self._sync_topics()

    @staticmethod
    def _topic_color(agent: str) -> int:
        return {
            "codex": 0x6FB9F0,
            "claude": 0xFFD67E,
            "devin": 0xCB86DB,
        }.get(agent, 0x8EEE98)

    @staticmethod
    def _topic_is_closed_error(exc: TelegramError) -> bool:
        message = str(exc).lower()
        return "topic_closed" in message or "topic closed" in message

    @staticmethod
    def _topic_is_missing_error(exc: TelegramError) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "message thread not found",
                "message_thread_invalid",
                "topic_id_invalid",
                "topic not found",
            )
        )

    def _ensure_topic(self, pane: AgentPane) -> dict[str, Any]:
        chat_id = self._group_chat_id()
        if chat_id is None:
            raise TelegramError("No private topic group is configured")
        topics = self._topics()
        record = topics.get(pane.topic_key)

        if isinstance(record, dict) and isinstance(record.get("thread_id"), int):
            thread_id = int(record["thread_id"])
            if record.get("closed"):
                try:
                    self.telegram.call(
                        "reopenForumTopic",
                        {"chat_id": chat_id, "message_thread_id": thread_id},
                    )
                except TelegramError as exc:
                    if self._topic_is_missing_error(exc):
                        topics.pop(pane.topic_key, None)
                        record = None
                    elif "not modified" not in str(exc).lower():
                        raise
                else:
                    record["closed"] = False
                    self._send_message(
                        chat_id,
                        f"↩ Agent returned: {pane.label}",
                        thread_id,
                        disable_notification=True,
                    )

        if not isinstance(record, dict) or not isinstance(record.get("thread_id"), int):
            created = self.telegram.call(
                "createForumTopic",
                {
                    "chat_id": chat_id,
                    "name": pane.topic_name,
                    "icon_color": self._topic_color(pane.agent),
                },
            )
            if not isinstance(created, dict) or not isinstance(
                created.get("message_thread_id"), int
            ):
                raise TelegramError("Telegram createForumTopic returned no topic id")
            record = {
                "thread_id": int(created["message_thread_id"]),
                "name": pane.topic_name,
                "pane_id": pane.pane_id,
                "label": pane.label,
                "closed": False,
            }
            topics[pane.topic_key] = record
            self._save_state()
            self._send_message(
                chat_id,
                f"🤖 Connected to {pane.label}\n"
                f"Status: {status_text(pane.status)}\n\n"
                "Messages in this topic go only to this tmux agent pane.",
                int(record["thread_id"]),
                disable_notification=True,
            )

        if record.get("name") != pane.topic_name:
            try:
                self.telegram.call(
                    "editForumTopic",
                    {
                        "chat_id": chat_id,
                        "message_thread_id": int(record["thread_id"]),
                        "name": pane.topic_name,
                    },
                )
            except TelegramError as exc:
                LOG.warning("Could not rename agent topic: %s", exc)
            else:
                record["name"] = pane.topic_name

        record["pane_id"] = pane.pane_id
        record["label"] = pane.label
        record["closed"] = False
        record.pop("missing_since", None)
        self._save_state()
        return record

    def _topic_for_thread(
        self, thread_id: int
    ) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        for key, record in self._topics().items():
            if isinstance(record, dict) and record.get("thread_id") == thread_id:
                return key, record
        return None, None

    def _send_to_agent_topic(
        self,
        pane: AgentPane,
        text: str,
        disable_notification: bool = False,
    ) -> None:
        chat_id = self._group_chat_id()
        if chat_id is None:
            return
        record = self._ensure_topic(pane)
        try:
            self._send_message(
                chat_id,
                text,
                int(record["thread_id"]),
                disable_notification=disable_notification,
            )
            return
        except TelegramError as exc:
            if self._topic_is_closed_error(exc):
                self.telegram.call(
                    "reopenForumTopic",
                    {
                        "chat_id": chat_id,
                        "message_thread_id": int(record["thread_id"]),
                    },
                )
                record["closed"] = False
                self._save_state()
            elif self._topic_is_missing_error(exc):
                self._topics().pop(pane.topic_key, None)
                self._save_state()
                record = self._ensure_topic(pane)
            else:
                raise
        self._send_message(
            chat_id,
            text,
            int(record["thread_id"]),
            disable_notification=disable_notification,
        )

    def _close_missing_topics(
        self, live_keys: set[str], now: float
    ) -> None:
        chat_id = self._group_chat_id()
        if chat_id is None:
            return
        for key, record in list(self._topics().items()):
            if key in live_keys or not isinstance(record, dict) or record.get("closed"):
                continue
            missing_since = record.get("missing_since")
            if not isinstance(missing_since, (int, float)):
                missing_since = now
                record["missing_since"] = missing_since
                self._save_state()
            if now - float(missing_since) < self.close_after:
                continue
            thread_id = record.get("thread_id")
            if not isinstance(thread_id, int):
                continue
            try:
                self._send_message(
                    chat_id,
                    "Agent process exited. Closing this topic; it will reopen "
                    "automatically if the same agent returns.",
                    thread_id,
                    disable_notification=True,
                )
                self.telegram.call(
                    "closeForumTopic",
                    {"chat_id": chat_id, "message_thread_id": thread_id},
                )
            except TelegramError as exc:
                if (
                    self._topic_is_missing_error(exc)
                    or self._topic_is_closed_error(exc)
                    or "not modified" in str(exc).lower()
                ):
                    record["closed"] = True
                else:
                    LOG.warning("Could not close stale agent topic: %s", exc)
                    continue
            else:
                record["closed"] = True
            self._save_state()

    def _post_completion(self, pane: AgentPane) -> None:
        output, source = self.tmux.last_output(pane)
        heading = f"❓ Ready for you\n{source}:"
        available = TELEGRAM_TEXT_LIMIT - len(heading) - 2
        text = heading + "\n\n" + trim_middle(output, max(200, available))
        self._send_to_agent_topic(pane, text)

    def _sync_topics(self) -> int:
        if self._group_chat_id() is None:
            return 0
        try:
            panes = self.tmux.agents()
        except TmuxError as exc:
            LOG.warning("Agent topic refresh failed: %s", exc)
            return 0

        live_keys = {pane.topic_key for pane in panes}
        for pane in panes:
            try:
                self._ensure_topic(pane)
            except TelegramError as exc:
                LOG.warning("Could not create/reopen topic for %s: %s", pane.label, exc)

        self._close_missing_topics(live_keys, time.time())

        current_status = {pane.topic_key: pane.status for pane in panes}
        current_versions = {
            pane.topic_key: self.tmux.message_version(pane) for pane in panes
        }
        previous_status = self.state.get("agent_status", {})
        previous_versions = self.state.get("message_versions", {})
        initialized = bool(self.state.get("topic_sync_initialized"))

        completed: list[AgentPane] = []
        if initialized:
            for pane in panes:
                newly_completed = (
                    previous_status.get(pane.topic_key) == "working"
                    and pane.status in {"done", "ask"}
                )
                version = current_versions[pane.topic_key]
                new_message = (
                    bool(version)
                    and pane.topic_key in previous_versions
                    and version != previous_versions.get(pane.topic_key)
                    and pane.status in {"done", "ask"}
                )
                if newly_completed or new_message:
                    completed.append(pane)

        self.state["agent_status"] = current_status
        self.state["message_versions"] = current_versions
        self.state["topic_sync_initialized"] = True
        self._save_state()

        if self.notify:
            for pane in completed:
                try:
                    self._post_completion(pane)
                except (TelegramError, TmuxError) as exc:
                    LOG.warning("Could not publish agent reply: %s", exc)
        return len(panes)

    def _status_report(self) -> str:
        panes = self.tmux.agents()
        if not panes:
            return "No live Codex or Claude panes."
        return "\n".join(
            f"{status_icon(pane.status)} {pane.topic_name} — {status_text(pane.status)}"
            for pane in panes
        )

    def _acknowledge(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        try:
            self.telegram.call(
                "setMessageReaction",
                {
                    "chat_id": int(chat["id"]),
                    "message_id": int(message["message_id"]),
                    "reaction": [{"type": "emoji", "emoji": "👍"}],
                },
            )
        except (TelegramError, KeyError, TypeError, ValueError):
            # A group may have reactions disabled. Delivery still succeeded.
            pass

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = message.get("text")
        if not isinstance(text, str):
            return
        if self.allowed_user_id is None:
            self._pairing_reply(message)
            return
        if not self._is_authorized(sender.get("id")):
            LOG.warning("Ignored an unauthorized Telegram message")
            return

        chat_id = int(chat["id"])
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        remainder = parts[1] if len(parts) == 2 else ""

        if chat.get("type") == "private":
            if command == "/id":
                self._send_message(
                    chat_id, f"Your Telegram user ID: {sender.get('id')}"
                )
            else:
                self._send_message(
                    chat_id,
                    "This bot uses a private group with Topics.\n\n"
                    "Create the group, enable Topics, add this bot as an "
                    "administrator with Manage Topics permission, then send "
                    "/setup in the group's General topic.",
                )
            return

        if chat.get("type") != "supergroup":
            return
        if command == "/setup":
            self._configure_group(message)
            return
        if self._group_chat_id() != chat_id:
            self._reply(
                message,
                "This is not the configured private tmux group. Send /setup "
                "here if you intend to move the bridge to this group.",
            )
            return
        if command in {"/sync", "/agents", "/sessions"}:
            count = self._sync_topics()
            self._reply(message, f"✓ Synchronized {count} live agent topic(s).")
            return
        if command == "/status":
            self._reply(message, self._status_report())
            return
        if command == "/help":
            self._reply(
                message,
                "/sync — reconcile live agent topics\n"
                "/status — show agent states\n"
                "/send TEXT — send TEXT to this topic's agent\n\n"
                "Ordinary text and agent slash commands are sent literally to "
                "the agent mapped to the current topic.",
            )
            return

        thread_id = message.get("message_thread_id")
        if not isinstance(thread_id, int):
            self._reply(message, "Send agent messages inside an agent topic.")
            return
        key, record = self._topic_for_thread(thread_id)
        if key is None or record is None:
            self._reply(
                message,
                "This topic is not mapped to a tmux agent. Use /sync in the "
                "Control topic; bot-created agent topics are mapped automatically.",
            )
            return

        outgoing = remainder if command == "/send" else text
        if not outgoing.strip():
            self._reply(message, "There was no text to send.")
            return
        pane = next(
            (candidate for candidate in self.tmux.agents() if candidate.topic_key == key),
            None,
        )
        if pane is None:
            self._reply(
                message,
                "That agent is no longer running. This topic will close "
                "automatically unless it returns.",
            )
            return
        self.tmux.send_message(pane.pane_id, outgoing)
        self._acknowledge(message)

    def run(self) -> None:
        if self.allowed_user_id is None:
            LOG.warning("Pairing mode: only Telegram user/chat IDs will be shown")
        backoff = 1
        while True:
            try:
                result = self.telegram.call(
                    "getUpdates",
                    {
                        "offset": int(self.state.get("update_offset", 0)),
                        "timeout": self.poll_timeout,
                        "allowed_updates": ["message"],
                    },
                    http_timeout=self.poll_timeout + 10,
                )
                for update in result or []:
                    update_id = int(update.get("update_id", 0))
                    self.state["update_offset"] = update_id + 1
                    self._save_state()
                    try:
                        if isinstance(update.get("message"), dict):
                            self._handle_message(update["message"])
                    except (TelegramError, TmuxError) as exc:
                        LOG.warning("Telegram update could not be handled: %s", exc)
                self._sync_topics()
                backoff = 1
            except TelegramError as exc:
                LOG.warning("%s; retrying in %ss", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="list detected agents without contacting Telegram"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    repo_root = Path(__file__).resolve().parents[1]
    tmux = TmuxClient(repo_root, os.environ.get("TMUX_TELEGRAM_TMUX_BIN", "tmux"))
    if args.check:
        panes = tmux.agents()
        if not panes:
            print("No live Codex or Claude panes detected.")
        for pane in panes:
            print(f"{status_icon(pane.status)} {pane.pane_id}\t{pane.label}\t{pane.status}")
        return 0

    token = os.environ.get("TMUX_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        parser.error("TMUX_TELEGRAM_BOT_TOKEN is required")
    allowed_raw = os.environ.get("TMUX_TELEGRAM_ALLOWED_USER_ID", "").strip()
    try:
        allowed_user_id = int(allowed_raw) if allowed_raw else None
    except ValueError:
        parser.error("TMUX_TELEGRAM_ALLOWED_USER_ID must be an integer")

    state_path = Path(
        os.environ.get(
            "TMUX_TELEGRAM_STATE_FILE",
            str(Path.home() / ".local/state/tmux-telegram-bridge/state.json"),
        )
    ).expanduser()
    bridge = Bridge(
        TelegramClient(token),
        tmux,
        allowed_user_id,
        state_path,
        poll_timeout=int(os.environ.get("TMUX_TELEGRAM_POLL_TIMEOUT", "5")),
        notify=env_bool("TMUX_TELEGRAM_NOTIFY", True),
        close_after=int(os.environ.get("TMUX_TELEGRAM_CLOSE_AFTER", "60")),
    )
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
