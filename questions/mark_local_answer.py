#!/usr/bin/env python3
"""Mark a pending MuxPager choice answered by direct terminal input."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from questions.store import ChoiceStore  # noqa: E402


def main() -> int:
    pane_id = os.environ.get("TMUX_PANE", "").strip()
    if not pane_id.startswith("%"):
        return 0
    agent = sys.argv[1].lower() if len(sys.argv) > 1 else None
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    conversation_id = payload.get("session_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        return 0
    choice_dir = os.environ.get("MUXPAGER_CHOICE_DIR")
    store = ChoiceStore(Path(choice_dir).expanduser() if choice_dir else None)
    store.mark_local_answer(pane_id, agent, conversation_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
