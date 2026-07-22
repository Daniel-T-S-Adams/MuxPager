#!/usr/bin/env python3
"""Private Telegram inbox for live Codex and Claude panes in tmux."""

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
        self.list_script = repo_root / "plugins/tmux-agent-status/scripts/list-agent-panes.sh"
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
    ) -> None:
        self.telegram = telegram
        self.tmux = tmux
        self.allowed_user_id = allowed_user_id
        self.state_path = state_path
        self.poll_timeout = max(1, min(poll_timeout, 25))
        self.notify = notify
        self.state = self._load_state()

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

    def _is_authorized(self, user_id: Any, chat: dict[str, Any]) -> bool:
        if self.allowed_user_id is None:
            return False
        return user_id == self.allowed_user_id and chat.get("type") == "private"

    def _pairing_reply(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") != "private" or not chat.get("id"):
            return
        text = (
            "This bridge is in pairing mode and cannot expose tmux yet.\n\n"
            f"Your Telegram user ID: {sender.get('id')}\n"
            f"This private chat ID: {chat.get('id')}\n\n"
            "Set TMUX_TELEGRAM_ALLOWED_USER_ID to your user ID, then restart the service."
        )
        self.telegram.call("sendMessage", {"chat_id": chat["id"], "text": text})

    def _agent_keyboard(self, panes: list[AgentPane]) -> dict[str, Any]:
        rows = []
        for pane in panes:
            label = f"{status_icon(pane.status)} {pane.label}"
            rows.append(
                [{"text": trim_label(label, 58), "callback_data": f"p:{pane.pane_id}"}]
            )
        rows.append([{"text": "↻ Refresh", "callback_data": "list"}])
        return {"inline_keyboard": rows}

    def _pane_keyboard(self, pane: AgentPane, panes: list[AgentPane]) -> dict[str, Any]:
        rows = [
            [
                {"text": "← Agents", "callback_data": "list"},
                {"text": "↻ Refresh", "callback_data": f"r:{pane.pane_id}"},
            ]
        ]
        if len(panes) > 1:
            rows.append(
                [
                    {"text": "◀ Previous", "callback_data": f"b:{pane.pane_id}"},
                    {"text": "Next ▶", "callback_data": f"n:{pane.pane_id}"},
                ]
            )
        return {"inline_keyboard": rows}

    def _upsert_view(
        self,
        chat_id: int,
        text: str,
        keyboard: dict[str, Any],
        message_id: int | None = None,
    ) -> int:
        text = trim_middle(text, TELEGRAM_TEXT_LIMIT)
        if message_id is None and self.state.get("dashboard_chat_id") == chat_id:
            stored = self.state.get("dashboard_message_id")
            if isinstance(stored, int):
                message_id = stored

        if message_id is not None:
            try:
                result = self.telegram.call(
                    "editMessageText",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": text,
                        "reply_markup": keyboard,
                    },
                )
                if isinstance(result, dict):
                    message_id = int(result.get("message_id", message_id))
            except TelegramError as exc:
                if "message is not modified" not in str(exc).lower():
                    message_id = None

        if message_id is None:
            result = self.telegram.call(
                "sendMessage",
                {"chat_id": chat_id, "text": text, "reply_markup": keyboard},
            )
            if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
                raise TelegramError("Telegram sendMessage returned no message id")
            message_id = result["message_id"]

        self.state["dashboard_chat_id"] = chat_id
        self.state["dashboard_message_id"] = message_id
        self._save_state()
        return message_id

    def show_dashboard(
        self, chat_id: int, message_id: int | None = None, notice: str = ""
    ) -> None:
        panes = self.tmux.agents()
        selected = self.state.get("selected_pane")
        selected_pane = next((pane for pane in panes if pane.pane_id == selected), None)
        lines = ["🤖 tmux agent inbox"]
        if notice:
            lines.extend(["", notice])
        lines.extend(["", f"{len(panes)} live agent pane(s). Tap one to read it."])
        if selected_pane:
            lines.append(f"Messages currently go to: {selected_pane.label}")
        else:
            lines.append("Select an agent before sending a message.")
        lines.extend(["", "Ordinary Telegram text is pasted literally into the selected pane and Enter is sent."])
        self._upsert_view(chat_id, "\n".join(lines), self._agent_keyboard(panes), message_id)

    def show_agent(
        self,
        chat_id: int,
        pane_id: str,
        message_id: int | None = None,
        notice: str = "",
    ) -> None:
        panes = self.tmux.agents()
        pane = next((candidate for candidate in panes if candidate.pane_id == pane_id), None)
        if pane is None:
            self.state.pop("selected_pane", None)
            self._save_state()
            self.show_dashboard(chat_id, message_id, "That agent pane is no longer running.")
            return

        self.state["selected_pane"] = pane.pane_id
        self._save_state()
        output, source = self.tmux.last_output(pane)
        heading = (
            f"{status_icon(pane.status)} {pane.label}\n"
            f"Status: {status_text(pane.status)}\n"
            f"{source}:"
        )
        if notice:
            heading = f"{notice}\n\n{heading}"
        available = TELEGRAM_TEXT_LIMIT - len(heading) - 2
        text = heading + "\n\n" + trim_middle(output, max(200, available))
        self._upsert_view(chat_id, text, self._pane_keyboard(pane, panes), message_id)

    def _selected_agent(self) -> AgentPane | None:
        selected = self.state.get("selected_pane")
        return next((pane for pane in self.tmux.agents() if pane.pane_id == selected), None)

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = message.get("text")
        if not isinstance(text, str):
            return
        if self.allowed_user_id is None:
            self._pairing_reply(message)
            return
        if not self._is_authorized(sender.get("id"), chat):
            LOG.warning("Ignored an unauthorized Telegram message")
            return

        chat_id = int(chat["id"])
        self.state["authorized_chat_id"] = chat_id
        self._save_state()
        command, _, remainder = text.partition(" ")
        command = command.split("@", 1)[0].lower()

        if command in {"/start", "/agents", "/sessions"}:
            self.show_dashboard(chat_id)
            return
        if command in {"/tail", "/refresh"}:
            pane = self._selected_agent()
            if pane:
                self.show_agent(chat_id, pane.pane_id)
            else:
                self.show_dashboard(chat_id, notice="Select an agent first.")
            return
        if command == "/id":
            self.telegram.call(
                "sendMessage",
                {"chat_id": chat_id, "text": f"Your Telegram user ID: {sender.get('id')}"},
            )
            return
        if command == "/help":
            self.telegram.call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "/agents — show live agent panes\n"
                        "/tail — refresh the selected pane\n"
                        "/send TEXT — send TEXT to the selected agent\n"
                        "Any other text, including agent slash commands, is sent literally."
                    ),
                },
            )
            return

        outgoing = remainder if command == "/send" else text
        if not outgoing.strip():
            self.show_dashboard(chat_id, notice="There was no text to send.")
            return
        pane = self._selected_agent()
        if pane is None:
            self.show_dashboard(chat_id, notice="Select an agent before sending a message.")
            return
        self.tmux.send_message(pane.pane_id, outgoing)
        self.show_agent(chat_id, pane.pane_id, notice=f"✓ Message sent to {pane.label}")

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        sender = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if not self._is_authorized(sender.get("id"), chat):
            LOG.warning("Ignored an unauthorized Telegram callback")
            return

        callback_id = callback.get("id")
        if callback_id:
            self.telegram.call("answerCallbackQuery", {"callback_query_id": callback_id})
        chat_id = int(chat["id"])
        message_id = int(message["message_id"])
        self.state["authorized_chat_id"] = chat_id
        data = str(callback.get("data") or "")

        if data == "list":
            self.show_dashboard(chat_id, message_id)
            return
        action, separator, pane_id = data.partition(":")
        if not separator or action not in {"p", "r", "b", "n"}:
            return
        if action in {"p", "r"}:
            self.show_agent(chat_id, pane_id, message_id)
            return

        panes = self.tmux.agents()
        current = next((index for index, pane in enumerate(panes) if pane.pane_id == pane_id), None)
        if current is None or not panes:
            self.show_dashboard(chat_id, message_id, "That agent pane is no longer running.")
            return
        delta = -1 if action == "b" else 1
        self.show_agent(chat_id, panes[(current + delta) % len(panes)].pane_id, message_id)

    def _check_notifications(self) -> None:
        try:
            panes = self.tmux.agents()
        except TmuxError as exc:
            LOG.warning("Agent status refresh failed: %s", exc)
            return
        current_status = {pane.pane_id: pane.status for pane in panes}
        current_versions = {pane.pane_id: self.tmux.message_version(pane) for pane in panes}
        previous_status = self.state.get("agent_status", {})
        previous_versions = self.state.get("message_versions", {})
        initialized = bool(self.state.get("agent_status_initialized"))

        completed: list[AgentPane] = []
        if initialized:
            for pane in panes:
                newly_completed = (
                    previous_status.get(pane.pane_id) == "working"
                    and pane.status in {"done", "ask"}
                )
                new_message = (
                    bool(current_versions[pane.pane_id])
                    and current_versions[pane.pane_id] != previous_versions.get(pane.pane_id)
                    and pane.status in {"done", "ask"}
                )
                if newly_completed or new_message:
                    completed.append(pane)

        self.state["agent_status"] = current_status
        self.state["message_versions"] = current_versions
        self.state["agent_status_initialized"] = True
        self._save_state()

        chat_id = self.state.get("authorized_chat_id")
        if not self.notify or not isinstance(chat_id, int):
            return
        # Keep Telegram as a one-message inbox: simultaneous completions update
        # the same dashboard, leaving the most recent waiting agent visible.
        for pane in completed:
            try:
                self.show_agent(
                    chat_id,
                    pane.pane_id,
                    notice=f"❓ {pane.label} is ready for you",
                )
            except (TelegramError, TmuxError) as exc:
                LOG.warning("Could not publish agent notification: %s", exc)

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
                        "allowed_updates": ["message", "callback_query"],
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
                        elif isinstance(update.get("callback_query"), dict):
                            self._handle_callback(update["callback_query"])
                    except (TelegramError, TmuxError) as exc:
                        LOG.warning("Telegram update could not be handled: %s", exc)
                self._check_notifications()
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
    )
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
