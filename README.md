# my-tmux

My customized, self-contained tmux setup. The tmux plugin, agent hooks, Telegram
bridge, and hook configuration templates all live in this repository, with no
plugin manager or network fetch at startup.

## What's here

```
.tmux.conf                     # my tmux config (symlinked to ~/.tmux.conf)
config/codex-hooks.json        # global Codex lifecycle hook configuration
config/claude-hooks.json       # Claude Code hook configuration to merge
scripts/agent-sidebar-ensure.sh# keeps the agent-status sidebar permanent
scripts/tmux-telegram-bridge.py# private Telegram topics for live agent panes
systemd/                      # persistent Telegram bridge user service
plugins/tmux-agent-status/     # VENDORED plugin, tracked via git subtree
```

The [tmux-agent-status](https://github.com/samleeney/tmux-agent-status) plugin is
vendored under `plugins/` with `git subtree`, so it lives directly in this repo and
is editable in place. There is no TPM — `.tmux.conf` sources the plugin's entry
file directly. Nothing in the active setup is loaded from `~/.tmux/plugins` or
`~/.config/tmux/plugins`.

## Install on a new machine

```bash
git clone https://github.com/Daniel-T-S-Adams/my-tmux.git ~/my-tmux
ln -sfn ~/my-tmux/.tmux.conf ~/.tmux.conf
mkdir -p ~/.codex
ln -s ~/my-tmux/config/codex-hooks.json ~/.codex/hooks.json
tmux kill-server 2>/dev/null; tmux            # start fresh so the config loads
```

If `~/.codex/hooks.json` already exists, merge its hooks or replace it with the
symlink after preserving any unrelated hooks. Restart Codex and run `/hooks` to
review and trust the commands if prompted. Current Codex releases enable hooks by
default; older releases may also require this in `~/.codex/config.toml`:

```toml
[features]
hooks = true
```

Claude Code cannot load a separate hook file directly. Merge the `hooks` object
from `config/claude-hooks.json` into `~/.claude/settings.json`. Both templates
resolve hook scripts through installer-managed links under
`~/.local/libexec/tmux-agent-console`, so agent tracking never depends on a
fixed clone location or another plugin checkout.

The tracked configuration discovers its checkout path when tmux loads it and
exports that path to Codex and Claude as `TMUX_AGENT_PROJECT_ROOT`; it does not
depend on the repository being cloned to `~/my-tmux`.

## Telegram agent topics

The optional Telegram bridge uses one private forum group as a set of persistent
agent chats. The group contains a `Control` topic and one bot-created topic for
every live Codex or Claude pane:

```text
My tmux agents
├── Control
├── main · codex #0
├── tig-mining · codex #1
└── tr · codex #1
```

Text sent inside an agent topic is pasted literally into only that topic's tmux
pane, followed by Enter. `/send TEXT` is an explicit equivalent; other slash
commands such as `/review` pass through to the agent. A successful delivery gets
a 👍 reaction.

When an agent finishes, its `Stop` hook caches the exact
`last_assistant_message`. The bridge posts that reply as a fresh Telegram
message in the corresponding topic, producing a normal topic notification.
While an agent is working—or until an older running agent completes its first
post-installation turn—the bridge can fall back to its visible pane output.

Topic mappings use tmux session/window/pane position plus agent type, rather
than the temporary tmux pane ID. If tmux recreates a pane, its existing topic
and history are reused. When an agent exits, the bridge waits 60 seconds and
closes the topic without deleting it. Returning agents reopen their old topic.

The bridge accepts messages only from the configured Telegram user ID and only
uses the private forum group registered with `/setup`. It rejects groups with a
public username. It does not offer shell execution: incoming text goes through
a private tmux paste buffer, and the pane is revalidated immediately before the
paste.

### Set up the bot and private group

1. In Telegram, open `@BotFather`, send `/newbot`, and create one bot for the
   whole tmux group. Copy its authentication token.

2. Install the local private configuration and user service:

   ```bash
   ~/my-tmux/scripts/setup-telegram-bridge.sh
   ```

3. Edit `~/.config/tmux-telegram-bridge/env`, set
   `TMUX_TELEGRAM_BOT_TOKEN`, and start the service in safe pairing mode:

   ```bash
   systemctl --user enable --now tmux-telegram-bridge
   ```

4. Open a direct chat with the new bot and send `/id`. Put the returned number
   in `TMUX_TELEGRAM_ALLOWED_USER_ID`, then restart:

   ```bash
   systemctl --user restart tmux-telegram-bridge
   ```

5. Create a new **private group** in Telegram and add the bot. The finished
   group must contain exactly two members: you and the bot. If Telegram made
   you add another person while creating it, remove that person before setup.
   Do not assign the group a public username or share its invite link.

6. In the group settings, enable **Topics**. Promote the bot to
   **Administrator** and grant **Manage Topics** permission. No other admin
   permissions are required. If Telegram will not let you add the bot, enable
   group joining for it through BotFather's `/setjoingroups` setting.

7. In the group's `General` topic, send:

   ```text
   /setup
   ```

   The bridge verifies that the group is private, is a forum, and granted the
   bot the required permission. It renames `General` to `Control` and creates
   one topic for every currently live agent pane.

The Control topic supports:

- `/sync` — immediately reconcile topics with live tmux agents.
- `/status` — list the live agents and their states.
- `/help` — show the short command guide.

The real token, Telegram user ID, group ID, and topic mappings are never
committed. The token/user ID live in a mode-`0600` environment file; the group
and topic IDs are learned through `/setup` and stored in a mode-`0600` state
file. To keep the service running after every login closes:

```bash
sudo loginctl enable-linger "$(id -un)"
```

Useful diagnostics:

```bash
~/my-tmux/scripts/tmux-telegram-bridge.py --check
systemctl --user status tmux-telegram-bridge
journalctl --user -u tmux-telegram-bridge -f
```

## Customize the plugin

Edit files under `plugins/tmux-agent-status/` directly and commit them like any
other file in this repo. Reload a running tmux with `tmux source ~/.tmux.conf`
(or `tmux kill-server` for a clean reload).

## Pull upstream plugin updates

`upstream` points at the original plugin. Pull new releases whenever you want:

```bash
cd ~/my-tmux
git subtree pull --prefix plugins/tmux-agent-status upstream main --squash
```

If it's not already configured:

```bash
git remote add upstream https://github.com/samleeney/tmux-agent-status.git
```

Merge conflicts (where upstream changed a line you also customized) are resolved
like any git merge, then committed.

## Local customizations layered on top of upstream

- **Close without confirmation** — `Ctrl-X` closes sessions/windows/panes
  immediately (upstreamed as #38; kept here).
- **Permanent sidebar** — `.tmux.conf` hooks `scripts/agent-sidebar-ensure.sh` to
  every navigation event so the sidebar re-appears if it self-closes.
- **`prefix + C`** — prompt for a name and create a new *detached* background
  session (does not switch to it).
- **Reworked status icons** — the sidebar icon answers "is an agent running here,
  and is it busy?" for both Claude Code and Codex:
  - 🟡 yellow spinner — the agent is actively thinking/answering (`working`).
  - 🔴 red `?` — an agent is waiting on you: either asking a question/permission
    (`ask`) **or** finished and idle at the prompt (`done`). Multi-agent rows show
    a `?N` count.
  - 🟢 green `✓` — no tracked agent in the pane (plain shell / no agent).
  - `⏸` (snooze/wait) and `P` (parked) are kept as-is.
  Agent presence is validated from the pane's live process tree: running,
  sleeping, or I/O-waiting Claude/Codex processes remain tracked while stopped,
  zombie, dead, or exited agents are treated as no agent. Persisted hook files
  provide status only after that liveness check, so stale metadata cannot turn a
  Bash window into an agent window.
  This required emitting a distinct `ask` state (`Notification` hook) and, because
  Claude Code fires **no hook on ESC-interrupt**, an interrupt-reconciliation pass
  in the collector: a pane stuck at `working` that no longer shows Claude's
  "esc to interrupt" footer is flipped to `done` (→ red `?`). See
  `hooks/better-hook.sh`, `scripts/sidebar.sh`, `scripts/lib/collect.sh`
  (`_reconcile_interrupted`).
- **No sidebar INBOX** — `@agent-sidebar-inbox off` leaves just the SESSIONS tree.
