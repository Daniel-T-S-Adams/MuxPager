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
   systemctl --user is-active tmux-telegram-bridge
   ```

   The second command should print `active`. If the bot cannot be added to
   groups later, open `/setjoingroups` in BotFather and allow group joins for
   this bot.

4. Send `/id` to the bot in a direct chat. Before an allowed user ID is saved,
   pairing mode intentionally answers only this command. Add the returned ID to
   the same file and save it:

   ```ini
   TMUX_TELEGRAM_ALLOWED_USER_ID=your-numeric-id
   ```

5. Restart the service:

   ```bash
   systemctl --user restart tmux-telegram-bridge
   ```

6. Create a private Telegram group containing exactly you and the bot. Enable
   Topics and grant the bot administrator permission to **Manage Topics**. If
   Telegram requires another person when creating the group, remove them before
   continuing.
7. Send `/setup` in General. General becomes Control; send `/status` there and
   confirm that your live agents are listed.

The bridge rejects non-forum groups, public groups, groups with additional
members, and bots without topic-management permission.

Telegram's Bot Privacy Mode can remain enabled: an administrator receives the
group messages the bridge needs. Unmute the group or individual agent topics if
you want a phone notification when an agent finishes.

If this machine should keep the bridge running after you log out (common on a
remote server), enable user lingering once:

```bash
sudo loginctl enable-linger "$(id -un)"
```

This is unnecessary when your user session remains logged in.

## Daily use

Open an agent topic and send ordinary text. The bot validates the mapping, pastes
the text literally into the live agent pane, sends Enter, and reacts with 👍.

Unknown slash commands such as `/review` are passed literally to the agent.
`/send TEXT` is an explicit equivalent for ordinary text.

Prompts entered directly in Codex or Claude through tmux are posted silently as
`You (tmux)` messages in the matching topic. Prompts longer than Telegram's
per-message limit are split across numbered messages without truncation.
Prompts originating in Telegram are recognized and are not posted a second
time.

When the agent completes a turn, its exact final message is posted as a fresh
topic message. Ensure the group or individual topic is not muted if you want a
push notification.

When Codex or Claude asks one question with two to six possible answers, the
agent ends its turn and the bridge posts an inline button for each option in the
same topic. Tap one button. MuxPager records the first valid tap and delivers
the selected label to that exact agent as its next user message. Use **Cancel**
when none of the choices should be assumed.

Only one choice question can be active per agent topic. While its buttons are
active, ordinary Telegram text is rejected so it cannot race the selection. If
you answer directly in tmux instead, the buttons are removed and marked as
answered locally. A saved button selection waits when the agent is still
working—for example on a long subprocess—and is delivered only when the current
turn is idle. Pending questions survive a bridge restart and do not time out;
they expire only if that exact agent pane exits or a newer question replaces
them.

The installer registers the local `muxpager` question tool with both installed
agent CLIs. Existing Codex and Claude processes must be restarted once after
installation so they can discover it. The feature uses your existing Telegram
bot, private group, and topics; there is no extra BotFather step.

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

First verify the local installation and bridge:

```bash
scripts/doctor.sh
systemctl --user is-active tmux-telegram-bridge
```

The doctor should report all required checks as passed, and the service command
should print `active`. In Control, `/status` should list the same live agents as
the tmux sidebar.

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

To test choices, ask either agent:

```text
Ask me which test target to use: Unit tests or Full suite. Use the MuxPager
single-choice question tool.
```

Expect two Telegram buttons in that agent's topic. Tap one and confirm that the
agent resumes with that selection. Then repeat and type an answer directly in
tmux; the Telegram buttons should become inactive and say the question was
answered locally.

## Diagnostics

```bash
scripts/doctor.sh
systemctl --user status tmux-telegram-bridge
journalctl --user -u tmux-telegram-bridge -f
~/.local/bin/tmux-telegram-bridge --check
```

The service logs never intentionally include the bot token.
