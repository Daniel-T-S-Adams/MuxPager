#!/usr/bin/env python3
"""Install or remove this project's managed tmux and agent configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


PROJECT_NAME = "tmux-agent-console"
TMUX_BEGIN = "# >>> tmux-agent-console managed configuration >>>"
TMUX_END = "# <<< tmux-agent-console managed configuration <<<"
MANAGED_HOOK_MARKERS = (
    "/.local/libexec/tmux-agent-console/hooks/",
    "/plugins/tmux-agent-status/hooks/",
)


def home_dir() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser().resolve()


def config_home() -> Path:
    return Path(
        os.environ.get("XDG_CONFIG_HOME", str(home_dir() / ".config"))
    ).expanduser()


def state_path() -> Path:
    return config_home() / PROJECT_NAME / "install.json"


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def json_load(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return payload


def json_write(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(payload, indent=2) + "\n", mode)


def tmux_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise RuntimeError("The project path cannot contain a newline")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def strip_tmux_block(content: str) -> str:
    while TMUX_BEGIN in content:
        before, remainder = content.split(TMUX_BEGIN, 1)
        if TMUX_END not in remainder:
            raise RuntimeError("The managed block in ~/.tmux.conf is incomplete")
        _, after = remainder.split(TMUX_END, 1)
        content = before.rstrip() + "\n" + after.lstrip("\n")
    return content.rstrip()


def resolve_write_path(path: Path, own_target: Path) -> tuple[Path, str]:
    if path.is_symlink():
        resolved = path.resolve()
        if resolved == own_target.resolve():
            path.unlink()
            return path, "legacy-project-symlink"
        if not resolved.exists():
            raise RuntimeError(f"Refusing to modify broken symlink: {path}")
        return resolved, "external-symlink"
    return path, "regular" if path.exists() else "missing"


def install_tmux(project_root: Path) -> dict[str, str]:
    entry_path = home_dir() / ".tmux.conf"
    write_path, original_kind = resolve_write_path(
        entry_path, project_root / ".tmux.conf"
    )
    existing = write_path.read_text(encoding="utf-8") if write_path.exists() else ""
    existing = strip_tmux_block(existing)
    block = (
        f"{TMUX_BEGIN}\n"
        f"source-file {tmux_quote(str(project_root / '.tmux.conf'))}\n"
        f"{TMUX_END}"
    )
    combined = f"{existing}\n\n{block}\n" if existing else f"{block}\n"
    atomic_write(write_path, combined)
    return {
        "entry_path": str(entry_path),
        "write_path": str(write_path),
        "original_kind": original_kind,
    }


def hook_is_managed(hook: Any) -> bool:
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    return any(marker in command for marker in MANAGED_HOOK_MARKERS)


def remove_managed_hooks(payload: dict[str, Any]) -> dict[str, Any]:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return payload
    cleaned_events: dict[str, Any] = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            cleaned_events[event] = entries
            continue
        cleaned_entries: list[Any] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                cleaned_entries.append(entry)
                continue
            cleaned_entry = dict(entry)
            cleaned_entry["hooks"] = [
                hook for hook in entry["hooks"] if not hook_is_managed(hook)
            ]
            if cleaned_entry["hooks"]:
                cleaned_entries.append(cleaned_entry)
        if cleaned_entries:
            cleaned_events[event] = cleaned_entries
    if cleaned_events:
        payload["hooks"] = cleaned_events
    else:
        payload.pop("hooks", None)
    return payload


def merge_hook_template(
    existing: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    existing = remove_managed_hooks(existing)
    target_hooks = existing.setdefault("hooks", {})
    if not isinstance(target_hooks, dict):
        raise RuntimeError("The existing 'hooks' value must be a JSON object")
    source_hooks = template.get("hooks")
    if not isinstance(source_hooks, dict):
        raise RuntimeError("The project hook template has no hooks object")
    for event, entries in source_hooks.items():
        if not isinstance(entries, list):
            raise RuntimeError(f"The project hook event {event} must be a list")
        current = target_hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise RuntimeError(f"The existing hook event {event} must be a list")
        current.extend(entries)
    return existing


def install_hook_config(
    destination: Path, template_path: Path, project_root: Path
) -> dict[str, str]:
    write_path, original_kind = resolve_write_path(destination, template_path)
    existing = json_load(write_path)
    template = json_load(template_path)
    merged = merge_hook_template(existing, template)
    json_write(write_path, merged)
    return {
        "entry_path": str(destination),
        "write_path": str(write_path),
        "original_kind": original_kind,
    }


def install(project_root: Path) -> None:
    project_root = project_root.resolve()
    previous = json_load(state_path())
    metadata = {
        "project_root": str(project_root),
        "tmux": install_tmux(project_root),
        "codex": install_hook_config(
            home_dir() / ".codex" / "hooks.json",
            project_root / "config" / "codex-hooks.json",
            project_root,
        ),
        "claude": install_hook_config(
            home_dir() / ".claude" / "settings.json",
            project_root / "config" / "claude-hooks.json",
            project_root,
        ),
    }
    if previous.get("project_root") == str(project_root):
        for section in ("tmux", "codex", "claude"):
            old_section = previous.get(section)
            new_section = metadata[section]
            if (
                isinstance(old_section, dict)
                and old_section.get("write_path") == new_section.get("write_path")
                and isinstance(old_section.get("original_kind"), str)
            ):
                new_section["original_kind"] = old_section["original_kind"]
    json_write(state_path(), metadata)


def remove_tmux(metadata: dict[str, Any]) -> None:
    write_path = Path(str(metadata.get("write_path", home_dir() / ".tmux.conf")))
    if not write_path.exists():
        return
    content = strip_tmux_block(write_path.read_text(encoding="utf-8"))
    if content:
        atomic_write(write_path, content + "\n")
    elif metadata.get("original_kind") in {"missing", "legacy-project-symlink"}:
        write_path.unlink(missing_ok=True)
    else:
        atomic_write(write_path, "")


def remove_hook_config(metadata: dict[str, Any]) -> None:
    write_path = Path(str(metadata.get("write_path", "")))
    if not write_path.exists():
        return
    cleaned = remove_managed_hooks(json_load(write_path))
    if not cleaned and metadata.get("original_kind") in {
        "missing",
        "legacy-project-symlink",
    }:
        write_path.unlink(missing_ok=True)
    else:
        json_write(write_path, cleaned)


def uninstall() -> None:
    metadata = json_load(state_path())
    if not metadata:
        raise RuntimeError("No managed installation record was found")
    remove_tmux(dict(metadata.get("tmux") or {}))
    remove_hook_config(dict(metadata.get("codex") or {}))
    remove_hook_config(dict(metadata.get("claude") or {}))
    state_path().unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    try:
        if args.action == "install":
            install(args.project_root)
        else:
            uninstall()
    except (OSError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
