#!/usr/bin/env python3
"""Durable, process-safe storage for MuxPager choice requests."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from typing import Any, Callable, Iterator


REQUEST_ID_RE = re.compile(r"^[a-f0-9]{24}$")
ACTIVE_STATES = {"pending", "published", "selected", "delivering"}
FINAL_STATES = {"delivered", "answered_locally", "cancelled", "expired", "superseded"}
ALL_STATES = ACTIVE_STATES | FINAL_STATES
MAX_QUESTION_LENGTH = 1000
MAX_OPTION_LABEL_LENGTH = 80
MAX_OPTION_DESCRIPTION_LENGTH = 240
MIN_OPTIONS = 2
MAX_OPTIONS = 6


class ChoiceStoreError(RuntimeError):
    """A choice request was invalid or could not be stored safely."""


def default_choice_dir() -> Path:
    state_home = Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    ).expanduser()
    return state_home / "tmux-telegram-bridge" / "choices"


def topic_key(session: str, window_index: str, pane_index: str, agent: str) -> str:
    return "\x1f".join((session, window_index, pane_index, agent))


def validate_options(options: Any) -> list[dict[str, str]]:
    if not isinstance(options, list) or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ChoiceStoreError(
            f"options must contain between {MIN_OPTIONS} and {MAX_OPTIONS} entries"
        )

    normalized: list[dict[str, str]] = []
    labels: set[str] = set()
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            raise ChoiceStoreError(f"option {index + 1} must be an object")
        label = str(option.get("label") or "").strip()
        description = str(option.get("description") or "").strip()
        if not label:
            raise ChoiceStoreError(f"option {index + 1} has no label")
        if len(label) > MAX_OPTION_LABEL_LENGTH:
            raise ChoiceStoreError(
                f"option {index + 1} label exceeds {MAX_OPTION_LABEL_LENGTH} characters"
            )
        if len(description) > MAX_OPTION_DESCRIPTION_LENGTH:
            raise ChoiceStoreError(
                f"option {index + 1} description exceeds "
                f"{MAX_OPTION_DESCRIPTION_LENGTH} characters"
            )
        folded = label.casefold()
        if folded in labels:
            raise ChoiceStoreError("option labels must be unique")
        labels.add(folded)
        normalized.append(
            {"id": str(index), "label": label, "description": description}
        )
    return normalized


class ChoiceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or default_choice_dir()).expanduser()

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_root()
        lock_path = self.root / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _path(self, request_id: str) -> Path:
        if not REQUEST_ID_RE.fullmatch(request_id):
            raise ChoiceStoreError("invalid choice request id")
        return self.root / f"{request_id}.json"

    def _read_path(self, path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        request_id = payload.get("id")
        if not isinstance(request_id, str) or path.name != f"{request_id}.json":
            return None
        if payload.get("state") not in ALL_STATES:
            return None
        return payload

    def _write_locked(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("id") or "")
        destination = self._path(request_id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root, prefix=f".{request_id}.", suffix=".tmp", text=True
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _all_locked(self) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            payload = self._read_path(path)
            if payload is not None:
                requests.append(payload)
        return requests

    def all(self) -> list[dict[str, Any]]:
        with self._locked():
            return self._all_locked()

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._locked():
            return self._read_path(self._path(request_id))

    def create(
        self,
        *,
        pane_id: str,
        session: str,
        window_index: str,
        pane_index: str,
        agent: str,
        question: str,
        options: Any,
        now: float | None = None,
    ) -> dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            raise ChoiceStoreError("question must not be empty")
        if len(question) > MAX_QUESTION_LENGTH:
            raise ChoiceStoreError(
                f"question exceeds {MAX_QUESTION_LENGTH} characters"
            )
        if not pane_id.startswith("%"):
            raise ChoiceStoreError("choice requests require a live tmux pane")
        if agent not in {"codex", "claude"}:
            raise ChoiceStoreError("choice requests support only Codex and Claude")
        normalized_options = validate_options(options)
        created_at = float(now if now is not None else time.time())
        key = topic_key(session, window_index, pane_index, agent)

        with self._locked():
            for existing in self._all_locked():
                if existing.get("topic_key") != key:
                    continue
                if existing.get("state") not in ACTIVE_STATES:
                    continue
                existing["state"] = "superseded"
                existing["finished_at"] = created_at
                self._write_locked(existing)

            request_id = secrets.token_hex(12)
            payload: dict[str, Any] = {
                "id": request_id,
                "state": "pending",
                "created_at": created_at,
                "pane_id": pane_id,
                "session": session,
                "window_index": window_index,
                "pane_index": pane_index,
                "agent": agent,
                "topic_key": key,
                "question": question,
                "options": normalized_options,
            }
            self._write_locked(payload)
            return payload

    def update(
        self,
        request_id: str,
        mutate: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        with self._locked():
            payload = self._read_path(self._path(request_id))
            if payload is None:
                return None
            updated = mutate(dict(payload))
            if updated is None:
                return payload
            if updated.get("state") not in ALL_STATES:
                raise ChoiceStoreError("invalid choice request state")
            self._write_locked(updated)
            return updated

    def active_for_topic(self, key: str) -> dict[str, Any] | None:
        with self._locked():
            matches = [
                payload
                for payload in self._all_locked()
                if payload.get("topic_key") == key
                and payload.get("state") in ACTIVE_STATES
            ]
        if not matches:
            return None
        return max(matches, key=lambda payload: float(payload.get("created_at", 0)))

    def mark_local_answer(
        self, pane_id: str, agent: str | None = None, now: float | None = None
    ) -> list[dict[str, Any]]:
        finished_at = float(now if now is not None else time.time())
        changed: list[dict[str, Any]] = []
        with self._locked():
            for payload in self._all_locked():
                if payload.get("pane_id") != pane_id:
                    continue
                if agent is not None and payload.get("agent") != agent:
                    continue
                state = payload.get("state")
                if state not in {"pending", "published", "delivering"}:
                    continue
                if state == "delivering":
                    payload["state"] = (
                        "cancelled" if payload.get("cancel_requested") else "delivered"
                    )
                else:
                    payload["state"] = "answered_locally"
                payload["finished_at"] = finished_at
                self._write_locked(payload)
                changed.append(payload)
        return changed

    def prune(self, now: float | None = None, retain_seconds: int = 604800) -> int:
        cutoff = float(now if now is not None else time.time()) - max(
            0, retain_seconds
        )
        removed = 0
        with self._locked():
            for path in self.root.glob("*.json"):
                payload = self._read_path(path)
                if payload is None:
                    continue
                if payload.get("state") not in FINAL_STATES:
                    continue
                finished_at = float(
                    payload.get("finished_at", payload.get("created_at", 0))
                )
                if finished_at < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed
