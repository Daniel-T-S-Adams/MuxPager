# Security model

The Telegram bridge is a remote input path into interactive agent panes. Its
controls are designed to prevent that input from becoming a general remote
shell.

## Trust boundary

The bridge trusts:

- The local Unix user running tmux and the systemd user service.
- One exact Telegram numeric user ID.
- One private forum supergroup registered through `/setup`.

It does not trust group names, topic names, tmux window names, cached statuses,
or Telegram usernames as identity.

## Controls

- Pairing mode reveals only the sender's own numeric Telegram ID and never tmux
  contents.
- Every group message must come from the configured user ID and configured
  group.
- `/setup` requires a private group with no public username and exactly two
  members: the user and bot.
- Topics are created and mapped by the bridge; arbitrary topics cannot select a
  pane by name.
- Before every paste, the bridge resolves the stable mapping against the
  current live-agent list and verifies that the destination pane is not dead.
- Message text is written to a mode-`0600` temporary file, loaded into a private
  tmux buffer, pasted literally, and followed by Enter. The message is never
  evaluated by a shell belonging to the bridge.
- A local `UserPromptSubmit` hook queues the complete prompt in a mode-`0600`
  file. The bridge sends it only to the topic mapped to that validated live
  pane, then removes the queue file. Telegram-originated prompts carry a
  short-lived SHA-256 content marker so the hook does not mirror them twice.
- If the agent exits to Bash, the live-agent lookup fails and delivery is
  refused.
- Choice-button callback data contains only a random request ID and option
  number. Before accepting it, the bridge verifies the configured user, group,
  topic, Telegram message, durable request, and allowed option. The first valid
  answer wins.
- Choice questions and selections are stored in mode-`0600` files. A selected
  answer uses the same literal tmux-buffer delivery path as an ordinary
  Telegram message.
- `/new` discovers only Git working trees beneath the configured repository
  root. Discovery does not follow directory symlinks, and the resolved path is
  checked beneath that root again when each button is pressed.
- Repository-picker callbacks contain a random request ID and bounded index,
  not a client-supplied path. They are bound to the configured user, group,
  topic, and Telegram message and expire after 15 minutes.
- `/resume` candidates come only from closed local topic mappings and local
  Codex/Claude session metadata. The working directory is revalidated beneath
  the configured root, the conversation identity must be a UUID, and callbacks
  are bound to the configured user, group, topic, and Telegram message for 15
  minutes. Telegram cannot supply a resume ID, executable, or filesystem path.
- A resume always creates a dedicated detached tmux session. The typed command
  contains only the trusted executable, fixed agent resume syntax, and validated
  local conversation ID; the reported live ID must match before handoff.
- `/prune legacy` only selects closed pre-upgrade Codex/Claude topics and
  requires an inline confirmation bound to the configured user, group, topic,
  and Telegram message. The request expires after 15 minutes, and every topic
  is revalidated against live agents before irreversible deletion.
- Agent launch is restricted to installed Codex and Claude executables selected
  by the bridge. The absolute executable path is typed literally into a new
  detached tmux pane; neither repository names nor Telegram text are evaluated
  by a shell command.

An agent may itself choose to execute a command after reading a Telegram
message. Telegram therefore has the same authority as typing a prompt directly
into that agent; it does not bypass the agent's own approval or sandbox policy.

Because complete local prompts are mirrored, their text leaves the server and
is retained in the private Telegram topic. Do not type secrets into an agent
prompt unless storing that prompt in Telegram is acceptable.

The installer allows Claude to call only the exact
`mcp__muxpager__ask_choice` tool without another terminal approval prompt. That
tool can write a bounded local question record; it cannot run commands or send
input to tmux. Codex uses the tool's non-destructive MCP annotation and normal
tool policy. Actual input delivery remains exclusively in the authenticated
Telegram bridge.

## Secret storage

The token and allowed user ID live in:

```text
~/.config/tmux-telegram-bridge/env
```

The installer creates it with mode `0600`. Group/topic IDs and launch picker
state live in a separate mode-`0600` state file under `~/.local/state`.

Never commit either file or paste a token into an issue, shell command argument,
or chat. If a token is exposed:

1. Revoke it immediately with `@BotFather`.
2. Generate a replacement.
3. Update the private environment file.
4. Restart `tmux-telegram-bridge`.
5. Remove the secret from Git history before making the repository public.
