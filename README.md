# MuxPager

[![CI](https://github.com/Daniel-T-S-Adams/MuxPager/actions/workflows/ci.yml/badge.svg)](https://github.com/Daniel-T-S-Adams/MuxPager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-contained tmux workspace for running Codex and Claude across many
sessions, seeing which agents need attention, and continuing those conversations
from a private Telegram group.

The tmux interface and Telegram topics are two views of the same live agent
state. Both use the same process-liveness checks and exact end-of-turn messages,
so a shell is never presented as an agent and a long-running agent is not
mistaken for a dead one.

```text
tmux                                        Telegram
┌─────────────────────────────────────┐     My tmux agents
│ project session                     │     ├── Control
│ ┌─────────────────┬───────────────┐ │     ├── api · codex #0
│ │ Codex / Claude  │ SESSIONS      │ │     └── web · claude #1
│ │                 │ ? api         │ │
│ │                 │ ⠋ web         │ │     Messages and replies stay
│ └─────────────────┴───────────────┘ │     inside the matching topic.
└─────────────────────────────────────┘
```

## Features

- Permanent session sidebar and fast `fzf` switcher.
- Correct `working`, `waiting`, `parked`, and non-agent classification for
  Codex and Claude panes.
- Process-tree validation so exited, stopped, and zombie agents cannot leave a
  Bash pane looking like an agent.
- A private Telegram forum group with one persistent topic per live agent pane.
- Safe phone-to-agent messaging through private tmux paste buffers—never through
  a shell command.
- Fresh Telegram notifications containing the agent's exact final response.
- Topic lifecycle management: close after exit, retain history, and reopen when
  the same logical agent returns.
- Vendored plugin source with no plugin manager or network fetch at tmux startup.

## Requirements

- Linux with tmux, systemd user services, Python 3, Bash 4+, `fzf`, `ps`, and
  `pgrep`.
- Codex CLI, Claude Code, or both.
- A Telegram account for the phone interface.

Install the system dependencies first with the command for your distribution.
On Ubuntu or Debian:

```bash
sudo apt update
sudo apt install tmux fzf procps python3
```

On Fedora:

```bash
sudo dnf install tmux fzf procps-ng python3
```

On Arch Linux:

```bash
sudo pacman -S --needed tmux fzf procps-ng python
```

The installer checks every required command before changing your configuration.
Run `scripts/doctor.sh` after installation for an exact machine-level report.

## Install

Clone the repository anywhere and run the unified installer:

```bash
git clone https://github.com/Daniel-T-S-Adams/MuxPager.git
cd MuxPager
./install.sh
```

The installer:

- Adds one managed source block to `~/.tmux.conf` without replacing unrelated
  tmux settings.
- Merges the project hooks into `~/.codex/hooks.json` and
  `~/.claude/settings.json` without removing unrelated settings or hooks.
- Installs stable runtime links so the checkout can be moved and reinstalled.
- Installs the Telegram bridge, private environment file, and systemd user
  service.
- Starts Telegram automatically when a token is already configured.

It is idempotent: rerunning it updates paths and hooks without duplicating them.
After the first install:

1. Start a new tmux session, or reload the current server with
   `tmux source-file ~/.tmux.conf`.
2. Restart any Codex and Claude processes that were already open.
3. In Codex, open `/hooks`, review the four MuxPager user hooks, and trust them.
   Codex hooks are enabled by default; if you previously set
   `[features] hooks = false`, remove that override.
4. In Claude Code, open `/hooks` and confirm the MuxPager user hooks appear.
5. Run the machine checks:

```bash
scripts/doctor.sh
```

See the official [Codex hooks documentation](https://learn.chatgpt.com/docs/hooks.md)
and [Claude Code hooks documentation](https://code.claude.com/docs/en/hooks)
for the agent-specific hook controls.

## Connect Telegram

Telegram is part of the main workflow, but its credentials must be created by
you and remain outside Git:

1. Open `@BotFather`, run `/newbot`, and create one bot for this project.
2. Edit `~/.config/tmux-telegram-bridge/env` and set
   `TMUX_TELEGRAM_BOT_TOKEN`.
3. Start the bridge:

   ```bash
   systemctl --user enable --now tmux-telegram-bridge
   ```

4. Direct-message the bot with `/id`, copy the returned numeric ID into
   `TMUX_TELEGRAM_ALLOWED_USER_ID`, save the file, and restart the service.
5. Create a private group containing exactly you and the bot. If Telegram makes
   you add another person to create it, remove that person before `/setup`.
6. Enable Topics, then make the bot an administrator with **Manage Topics**.
7. Send `/setup` in the group's General topic, then send `/status` in Control.

`General` becomes `Control`, and the bridge creates one topic for every live
Codex or Claude pane. Text in an agent topic goes only to that pane; a 👍 confirms
delivery. Completed replies arrive as new messages in the same topic. Privacy
Mode can stay enabled because the bot is an administrator. If the bot cannot be
added to the group, use BotFather's `/setjoingroups` to allow it. Unmute the
group or its agent topics if you want phone notifications.

See [docs/telegram.md](docs/telegram.md) for lifecycle behavior, testing, privacy
checks, notifications, and troubleshooting.

## Use the console

| Key | Action |
| --- | --- |
| `prefix + S` | Open the hierarchical session/window/pane switcher |
| `prefix + o` | Focus or recreate the sidebar |
| `prefix + N` | Jump to the next agent waiting for attention |
| `prefix + W` | Snooze the selected target |
| `prefix + p` | Park or unpark the selected target |
| `prefix + C` | Create a named detached background session |

The interface uses these semantics:

| Indicator | Meaning |
| --- | --- |
| yellow spinner | A live agent is working, including waiting on a subprocess |
| red `?` | A live agent is asking for input or waiting at its prompt |
| green `✓` | No live agent is present; the pane is a normal shell or program |
| `⏸` | The target is snoozed |
| `P` | The target is parked |

In the Telegram `Control` topic:

- `/sync` reconciles topics with live tmux agents immediately.
- `/status` lists live agents and their states.
- `/help` displays the bot command guide.

## How it works

Agent hooks record lifecycle state and exact final messages under
`~/.cache/tmux-agent-status`. The collector validates those records against the
live tmux pane process tree. The sidebar, switcher, status line, and Telegram
bridge all consume that validated state.

`bin/muxpager-agent-list` is the stable integration boundary. It emits one
tab-separated row per live agent pane; Telegram does not depend directly on
collector internals.

See [docs/architecture.md](docs/architecture.md) for the data flow, state model,
and extension boundary.

## Security

- The bridge accepts messages only from one configured Telegram user ID.
- `/setup` requires a private forum supergroup containing exactly the user and
  bot, with no public username.
- Telegram text is loaded into a private tmux buffer, the destination pane is
  revalidated, and the buffer is pasted literally. It is never interpolated
  into a shell command.
- Messages are rejected when the mapped agent has exited, even if its Bash pane
  remains open.
- Bot credentials and learned topic IDs live in mode-`0600` files outside the
  repository.

Do not paste a bot token into an issue, commit, terminal command argument, or
chat transcript. See [docs/security.md](docs/security.md) for the complete trust
model and incident steps.

## Update or remove

Pull project updates and rerun the installer:

```bash
git pull
./install.sh
```

To remove managed tmux configuration, agent hooks, runtime links, and the
Telegram service:

```bash
./uninstall.sh
```

The uninstaller preserves Telegram credentials and topic state. It also
preserves all unrelated tmux, Codex, and Claude settings.

The agent-status core is vendored from `samleeney/tmux-agent-status` with
`git subtree`. Maintainers can refresh it with:

```bash
git remote add upstream https://github.com/samleeney/tmux-agent-status.git
git subtree pull --prefix plugins/tmux-agent-status upstream main --squash
```

## Develop

Run the complete local validation:

```bash
tests/run-all.sh
```

The suite covers the Telegram bridge, relocated installations, safe
install/uninstall behavior, and the vendored plugin's shell regressions.

For common failures, see [docs/troubleshooting.md](docs/troubleshooting.md).

The project is released under the [MIT License](LICENSE). The vendored
agent-status core retains upstream attribution in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
