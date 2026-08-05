#!/usr/bin/env python3
"""Resolve persisted Codex and Claude conversations to their working directory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Iterator


CONVERSATION_ID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.IGNORECASE,
)
MAX_METADATA_LINES = 200


def valid_conversation_id(conversation_id: str) -> bool:
    return bool(CONVERSATION_ID_RE.fullmatch(str(conversation_id or "")))


class SessionMetadataResolver:
    """Find a conversation's original cwd without trusting Telegram input."""

    def __init__(
        self,
        codex_home: Path | None = None,
        claude_home: Path | None = None,
    ) -> None:
        configured_codex_home = os.environ.get("CODEX_HOME", "").strip()
        configured_claude_home = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        self.codex_home = Path(
            codex_home
            or configured_codex_home
            or Path.home() / ".codex"
        ).expanduser()
        self.claude_home = Path(
            claude_home
            or configured_claude_home
            or Path.home() / ".claude"
        ).expanduser()

    def _candidate_files(self, agent: str, conversation_id: str) -> Iterator[Path]:
        if agent == "codex":
            roots = (
                self.codex_home / "sessions",
                self.codex_home / "archived_sessions",
            )
            pattern = f"*{conversation_id}.jsonl"
        elif agent == "claude":
            roots = (self.claude_home / "projects",)
            pattern = f"{conversation_id}.jsonl"
        else:
            return
        for root in roots:
            if not root.is_dir():
                continue
            yield from root.rglob(pattern)

    @staticmethod
    def _metadata_cwd(
        agent: str, payload: Any, conversation_id: str
    ) -> Path | None:
        if not isinstance(payload, dict):
            return None
        if agent == "codex":
            if payload.get("type") != "session_meta":
                return None
            metadata = payload.get("payload")
            if not isinstance(metadata, dict):
                return None
            stored_id = metadata.get("session_id") or metadata.get("id")
            cwd = metadata.get("cwd")
        else:
            stored_id = payload.get("sessionId")
            cwd = payload.get("cwd")
        if stored_id != conversation_id or not isinstance(cwd, str) or not cwd:
            return None
        return Path(cwd).expanduser()

    def resolve(self, agent: str, conversation_id: str) -> Path | None:
        if agent not in {"codex", "claude"} or not valid_conversation_id(
            conversation_id
        ):
            return None
        for path in self._candidate_files(agent, conversation_id):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for index, line in enumerate(handle):
                        if index >= MAX_METADATA_LINES:
                            break
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        cwd = self._metadata_cwd(agent, payload, conversation_id)
                        if cwd is not None:
                            return cwd
            except OSError:
                continue
        return None
