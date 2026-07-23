#!/usr/bin/env python3
"""Install, verify, or remove MuxPager's MCP server for supported agents."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


SERVER_NAME = "muxpager"


class McpConfigError(RuntimeError):
    """An existing MCP entry cannot be changed safely."""


def home_dir() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser().resolve()


def server_path() -> Path:
    return home_dir() / ".local/bin/muxpager-choice-server"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise McpConfigError(f"could not run {command[0]} MCP configuration") from exc


def codex_entry() -> dict[str, Any] | None:
    result = run(["codex", "mcp", "get", SERVER_NAME, "--json"])
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise McpConfigError("Codex returned invalid MCP configuration JSON") from exc
    return payload if isinstance(payload, dict) else None


def codex_owned(entry: dict[str, Any], executable: Path) -> bool:
    transport = entry.get("transport")
    return (
        isinstance(transport, dict)
        and transport.get("type") == "stdio"
        and transport.get("command") == str(executable)
    )


def install_codex(executable: Path) -> str:
    entry = codex_entry()
    if entry is not None:
        if not codex_owned(entry, executable):
            raise McpConfigError(
                "Codex already has an unrelated MCP server named 'muxpager'; "
                "remove or rename it before installing"
            )
        environment = entry.get("transport", {}).get("env")
        if isinstance(environment, dict) and environment.get(
            "MUXPAGER_AGENT"
        ) == "codex":
            return "already configured"
        removed = run(["codex", "mcp", "remove", SERVER_NAME])
        if removed.returncode != 0:
            raise McpConfigError("could not refresh the Codex MuxPager MCP entry")
    added = run(
        [
            "codex",
            "mcp",
            "add",
            SERVER_NAME,
            "--env",
            "MUXPAGER_AGENT=codex",
            "--",
            str(executable),
        ]
    )
    if added.returncode != 0:
        raise McpConfigError(
            "could not add the Codex MuxPager MCP server: "
            + (added.stderr.strip() or added.stdout.strip() or "unknown error")
        )
    return "configured"


def claude_config() -> tuple[Path, dict[str, Any]]:
    path = home_dir() / ".claude.json"
    if not path.exists():
        return path, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"{path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise McpConfigError(f"{path} must contain a JSON object")
    return path, payload


def claude_entry() -> dict[str, Any] | None:
    _, payload = claude_config()
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(SERVER_NAME)
    return entry if isinstance(entry, dict) else None


def claude_owned(entry: dict[str, Any], executable: Path) -> bool:
    return (
        entry.get("type", "stdio") == "stdio"
        and entry.get("command") == str(executable)
    )


def install_claude(executable: Path) -> str:
    entry = claude_entry()
    if entry is not None:
        if not claude_owned(entry, executable):
            raise McpConfigError(
                "Claude already has an unrelated user MCP server named "
                "'muxpager'; remove or rename it before installing"
            )
        environment = entry.get("env")
        if isinstance(environment, dict) and environment.get(
            "MUXPAGER_AGENT"
        ) == "claude":
            return "already configured"
        removed = run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME])
        if removed.returncode != 0:
            raise McpConfigError("could not refresh the Claude MuxPager MCP entry")
    added = run(
        [
            "claude",
            "mcp",
            "add",
            "--scope",
            "user",
            "--transport",
            "stdio",
            SERVER_NAME,
            "--env",
            "MUXPAGER_AGENT=claude",
            "--",
            str(executable),
        ]
    )
    if added.returncode != 0:
        raise McpConfigError(
            "could not add the Claude MuxPager MCP server: "
            + (added.stderr.strip() or added.stdout.strip() or "unknown error")
        )
    return "configured"


def remove_codex(executable: Path) -> str:
    entry = codex_entry()
    if entry is None:
        return "not configured"
    if not codex_owned(entry, executable):
        return "preserved unrelated entry"
    result = run(["codex", "mcp", "remove", SERVER_NAME])
    if result.returncode != 0:
        raise McpConfigError("could not remove the Codex MuxPager MCP entry")
    return "removed"


def remove_claude(executable: Path) -> str:
    entry = claude_entry()
    if entry is None:
        return "not configured"
    if not claude_owned(entry, executable):
        return "preserved unrelated entry"
    result = run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME])
    if result.returncode != 0:
        raise McpConfigError("could not remove the Claude MuxPager MCP entry")
    return "removed"


def check_codex(executable: Path) -> bool:
    entry = codex_entry()
    if entry is None or not codex_owned(entry, executable):
        return False
    environment = entry.get("transport", {}).get("env")
    return (
        isinstance(environment, dict)
        and environment.get("MUXPAGER_AGENT") == "codex"
    )


def check_claude(executable: Path) -> bool:
    entry = claude_entry()
    if entry is None or not claude_owned(entry, executable):
        return False
    environment = entry.get("env")
    return (
        isinstance(environment, dict)
        and environment.get("MUXPAGER_AGENT") == "claude"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "check"))
    args = parser.parse_args()
    executable = server_path()
    failures = 0
    if args.action in {"install", "check"} and not executable.is_file():
        print(f"MuxPager MCP: choice server is missing at {executable}", file=os.sys.stderr)
        return 1

    for agent, command, install_fn, remove_fn, check_fn in (
        ("Codex", "codex", install_codex, remove_codex, check_codex),
        ("Claude", "claude", install_claude, remove_claude, check_claude),
    ):
        if shutil.which(command) is None:
            if args.action == "check":
                continue
            print(f"{agent} MCP: skipped ({command} is not installed)")
            continue
        try:
            if args.action == "install":
                result = install_fn(executable)
                print(f"{agent} MCP: {result}")
            elif args.action == "uninstall":
                result = remove_fn(executable)
                print(f"{agent} MCP: {result}")
            elif check_fn(executable):
                print(f"{agent} MCP: configured")
            else:
                print(f"{agent} MCP: missing or points elsewhere")
                failures += 1
        except McpConfigError as exc:
            print(f"{agent} MCP: {exc}", file=os.sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
