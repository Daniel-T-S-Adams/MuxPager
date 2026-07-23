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

## Topic did not reopen

The stable identity includes session name, window index, pane index, and agent
type. Reopening Claude or Codex in another pane correctly creates a different
topic. Run `/status` in Control and compare with `bin/muxpager-agent-list`.
