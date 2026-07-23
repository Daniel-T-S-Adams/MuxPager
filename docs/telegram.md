# Telegram topics

The Telegram interface mirrors the agents visible in tmux. One bot manages one
private forum group; each live Codex or Claude pane has its own topic.

## Setup

1. Create a bot with `@BotFather` using `/newbot`.
2. Put its token in `~/.config/tmux-telegram-bridge/env`:

   ```ini
   TMUX_TELEGRAM_BOT_TOKEN=your-token
   ```

3. Enable the bridge:

   ```bash
   systemctl --user enable --now tmux-telegram-bridge
   ```

4. Send `/id` to the bot in a direct chat. Add the returned ID to the same file:

   ```ini
   TMUX_TELEGRAM_ALLOWED_USER_ID=your-numeric-id
   ```

5. Restart the service:

   ```bash
   systemctl --user restart tmux-telegram-bridge
   ```

6. Create a private Telegram group containing exactly you and the bot. Enable
   Topics and grant the bot administrator permission to **Manage Topics**.
7. Send `/setup` in General.

The bridge rejects non-forum groups, public groups, groups with additional
members, and bots without topic-management permission.

## Daily use

Open an agent topic and send ordinary text. The bot validates the mapping, pastes
the text literally into the live agent pane, sends Enter, and reacts with 👍.

Unknown slash commands such as `/review` are passed literally to the agent.
`/send TEXT` is an explicit equivalent for ordinary text.

When the agent completes a turn, its exact final message is posted as a fresh
topic message. Ensure the group or individual topic is not muted if you want a
push notification.

The Control topic provides:

- `/sync` — reconcile all live agent topics now.
- `/status` — list current agents and states.
- `/help` — display the short command guide.

## Lifecycle

When an agent exits but the pane returns to Bash:

- The bridge immediately stops treating the pane as an agent.
- Messages are rejected rather than pasted into Bash.
- The topic remains open for `TMUX_TELEGRAM_CLOSE_AFTER` seconds, defaulting to
  60, to avoid flicker during quick restarts.
- The topic then receives a silent exit note and closes without losing history.

Starting the same agent type in the same tmux session/window/pane position
reopens that topic. A different position creates a new topic.

Reopening preserves Telegram history. It does not itself restore the Codex or
Claude conversation; use the agent CLI's resume functionality when that is
needed.

## End-to-end test

In a waiting agent topic, send:

```text
Reply with exactly: TELEGRAM ROUND TRIP OK
```

Expect a 👍, activity in the corresponding tmux pane, and a fresh Telegram reply
in that same topic.

To test long-running work:

```text
Run sleep 20 in the terminal, then reply exactly: LONG JOB COMPLETE
```

The topic should remain open and `/status` should show the agent working until
the final reply arrives.

## Diagnostics

```bash
scripts/doctor.sh
systemctl --user status tmux-telegram-bridge
journalctl --user -u tmux-telegram-bridge -f
~/.local/bin/tmux-telegram-bridge --check
```

The service logs never intentionally include the bot token.
