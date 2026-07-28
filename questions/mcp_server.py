#!/usr/bin/env python3
"""Dependency-free MCP server that publishes one MuxPager choice question."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from questions.store import (  # noqa: E402
    ChoiceStore,
    ChoiceStoreError,
    MAX_OPTION_DESCRIPTION_LENGTH,
    MAX_OPTION_LABEL_LENGTH,
    MAX_OPTIONS,
    MAX_QUESTION_LENGTH,
    MIN_OPTIONS,
)


SERVER_NAME = "muxpager"
SERVER_VERSION = "0.1.0"
TOOL_NAME = "ask_choice"
INSTRUCTIONS = (
    "When the user must select exactly one answer from a short list, call "
    "ask_choice instead of Codex request_user_input or Claude AskUserQuestion. "
    "After ask_choice succeeds, end the current turn. MuxPager will deliver "
    "the selected answer as the user's next message."
)


def tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "title": "Ask through MuxPager",
        "description": (
            "Ask the user one single-choice question through buttons in the "
            "matching MuxPager Telegram topic. Supply 2-6 options. Use this "
            "instead of a native terminal choice picker. After this tool "
            "succeeds, stop and wait: the selected option will arrive as the "
            "next user message."
        ),
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "required": ["question", "options"],
            "properties": {
                "question": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_QUESTION_LENGTH,
                    "description": "The single question the user must answer.",
                },
                "options": {
                    "type": "array",
                    "minItems": MIN_OPTIONS,
                    "maxItems": MAX_OPTIONS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["label", "description"],
                        "properties": {
                            "label": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_OPTION_LABEL_LENGTH,
                            },
                            "description": {
                                "type": "string",
                                "maxLength": MAX_OPTION_DESCRIPTION_LENGTH,
                            },
                        },
                    },
                },
            },
        },
        "annotations": {
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


def resolve_pane(tmux_bin: str = "tmux") -> dict[str, str]:
    pane_id = os.environ.get("TMUX_PANE", "").strip()
    if not pane_id.startswith("%"):
        raise ChoiceStoreError("ask_choice must run inside a tmux agent pane")
    try:
        result = subprocess.run(
            [
                tmux_bin,
                "display-message",
                "-p",
                "-t",
                pane_id,
                "#{pane_dead}\t#{pane_id}\t#{session_name}\t"
                "#{window_index}\t#{pane_index}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ChoiceStoreError("could not resolve the current tmux pane") from exc
    fields = result.stdout.rstrip("\n").split("\t")
    if len(fields) != 5 or fields[0] != "0" or fields[1] != pane_id:
        raise ChoiceStoreError("the current tmux pane is no longer live")
    return {
        "pane_id": fields[1],
        "session": fields[2],
        "window_index": fields[3],
        "pane_index": fields[4],
    }


def resolve_conversation_id(pane: dict[str, str], agent: str) -> str:
    status_root = Path(
        os.environ.get(
            "MUXPAGER_STATUS_DIR",
            str(Path.home() / ".cache" / "tmux-agent-status"),
        )
    ).expanduser()
    identity_file = (
        status_root
        / "panes"
        / f"{pane['session']}_{pane['pane_id']}.conversation"
    )
    try:
        cached_agent, conversation_id = identity_file.read_text(
            encoding="utf-8"
        ).splitlines()[:2]
    except (OSError, ValueError):
        raise ChoiceStoreError(
            "the agent has not reported its conversation identity yet"
        ) from None
    if cached_agent != agent or not conversation_id:
        raise ChoiceStoreError(
            "the agent has not reported its conversation identity yet"
        )
    return conversation_id


def create_choice(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ChoiceStoreError("ask_choice arguments must be an object")
    agent = os.environ.get("MUXPAGER_AGENT", "").strip().lower()
    pane = resolve_pane(os.environ.get("MUXPAGER_TMUX_BIN", "tmux"))
    conversation_id = resolve_conversation_id(pane, agent)
    store = ChoiceStore(
        Path(os.environ["MUXPAGER_CHOICE_DIR"]).expanduser()
        if os.environ.get("MUXPAGER_CHOICE_DIR")
        else None
    )
    choice = store.create(
        **pane,
        agent=agent,
        conversation_id=conversation_id,
        question=arguments.get("question", ""),
        options=arguments.get("options"),
    )
    option_lines = "\n".join(
        f"{index + 1}. {option['label']}"
        + (f" — {option['description']}" if option["description"] else "")
        for index, option in enumerate(choice["options"])
    )
    message = (
        f"Choice request {choice['id']} was queued for MuxPager.\n\n"
        f"{choice['question']}\n{option_lines}\n\n"
        "End this turn now. Do not choose on the user's behalf and do not ask "
        "the same question with a native terminal picker. The user's selection "
        "will arrive as a new user message."
    )
    structured = {
        "status": "waiting_for_user",
        "request_id": choice["id"],
        "question": choice["question"],
        "options": [option["label"] for option in choice["options"]],
    }
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": message}],
        "structuredContent": structured,
        "isError": False,
    }


def request_result(message: dict[str, Any]) -> dict[str, Any]:
    method = message.get("method")
    params = message.get("params")
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "protocolVersion": requested or "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [tool_definition()]}
    if method == "tools/call":
        if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
            return {
                "content": [{"type": "text", "text": "Unknown MuxPager tool."}],
                "isError": True,
            }
        try:
            return create_choice(params.get("arguments"))
        except ChoiceStoreError as exc:
            return {
                "resultType": "complete",
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
    raise KeyError(str(method))


def write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        request_id = message.get("id")
        if request_id is None:
            continue
        try:
            result = request_result(message)
        except KeyError:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {message.get('method')}",
                    },
                }
            )
            continue
        except Exception as exc:  # Keep protocol failures off stdout.
            print(f"MuxPager choice server error: {exc}", file=sys.stderr)
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": "Internal server error"},
                }
            )
            continue
        write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
