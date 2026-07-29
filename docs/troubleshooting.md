# Troubleshooting

Start with:

```bash
scripts/doctor.sh
```

It checks required commands, managed configuration, agent discovery, Telegram
credentials, service health, and group pairing without printing secrets.

## An agent is missing

```bash
bin/muxpager-agent-list
```

If the pane is absent, verify that Codex or Claude is actually running—not just
that the window has an agent-like name—and restart that agent after installation
so it inherits the managed hooks.

## A Bash pane appears as an agent

Run `bin/muxpager-agent-list`. Persisted hook state is ignored unless the pane's
process tree contains a live agent. If the pane still appears, capture:

```bash
tmux display-message -p -t <pane-id> '#{pane_pid} #{pane_dead}'
ps -eo pid=,ppid=,stat=,args=
```

Do not delete the entire status cache as a first response; it may contain state
for unrelated live agents.

## Telegram `/id` does nothing

Check the service:

```bash
systemctl --user status tmux-telegram-bridge
journalctl --user -u tmux-telegram-bridge -n 50 --no-pager
```

The service cannot poll Telegram until `TMUX_TELEGRAM_BOT_TOKEN` is saved in
`~/.config/tmux-telegram-bridge/env`.

## A Telegram message gets 👍 but no reply

The reaction confirms delivery into the pane, not agent completion. Inspect the
matching tmux pane for an approval prompt, interrupted turn, or network error.

## Telegram pasted text but did not submit it

Verify that the pane is in the agent's normal prompt mode and that the Telegram
bridge is current:

```bash
git pull
./install.sh
systemctl --user restart tmux-telegram-bridge
```

Then watch the pane and service log during another short round-trip test.

## A choice question stays in the terminal

Restart the affected Codex or Claude process after running `./install.sh`.
Existing agents do not discover newly registered MCP tools mid-session. Check
the registration with:

```bash
scripts/doctor.sh
codex mcp get muxpager --json
claude mcp get muxpager
```

Both installed agents should point at
`~/.local/bin/muxpager-choice-server`. Also verify that the Telegram bridge is
active. Native single-choice tools are redirected to MuxPager; if an agent
still opens a terminal picker, mention the `MuxPager ask_choice` tool explicitly
and inspect its `/hooks` view for the managed PreToolUse hook.

## `/new` cannot find a repository

The picker lists only Git working trees beneath `TMUX_TELEGRAM_REPO_ROOT`, which
defaults to the service user's home directory (`/root` on this server). Confirm
the directory contains a `.git` directory or file, then narrow by any part of
its relative path:

```text
/new repository-name
```

Common cache, dependency, build, and virtual-environment directories are not
scanned. If you changed the root in
`~/.config/tmux-telegram-bridge/env`, restart the service. If startup remains at
the waiting message, attach to the displayed tmux target and complete any agent
login or repository-trust prompt.

## Topic did not reopen

The stable identity combines agent type with the Codex or Claude session ID.
Resuming that conversation in another pane should reuse its topic; starting a
new conversation in the same pane should create another. Run `/status` in
Control and compare with the final `conversation_id` field from
`bin/muxpager-agent-list`. If the field is briefly empty after upgrading,
submit a prompt or wait for the next hook event; delivery remains paused rather
than falling back to pane position.
