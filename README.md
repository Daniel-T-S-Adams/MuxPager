# my-tmux

My customized, self-contained tmux setup. The tmux plugin, agent hooks, and hook
configuration templates all live in this repository, with no plugin manager or
network fetch at startup.

## What's here

```
.tmux.conf                     # my tmux config (symlinked to ~/.tmux.conf)
config/codex-hooks.json        # global Codex lifecycle hook configuration
config/claude-hooks.json       # Claude Code hook configuration to merge
scripts/agent-sidebar-ensure.sh# keeps the agent-status sidebar permanent
scripts/tmux-telegram-bridge.py# private phone inbox for live agent panes
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
invoke hook scripts from `~/my-tmux/plugins/tmux-agent-status/hooks/`, so agent
tracking never depends on another plugin checkout.

> The config references `~/my-tmux` by absolute path. If you clone elsewhere,
> update the paths in `.tmux.conf` (the sidebar script itself is path-portable).

## Telegram agent inbox

The optional Telegram bridge provides a private, single-message inbox for the
live Codex and Claude panes on this tmux server:

- `/agents` displays one inline button per live agent pane.
- Tapping an agent shows its latest response. `Previous`, `Next`, and `Refresh`
  let you click through the panes without filling the chat with old output.
- Ordinary Telegram text is pasted literally into the selected pane, followed
  by Enter. `/send TEXT` is an explicit equivalent; other slash commands such
  as `/review` are passed through to the selected agent.
- When an agent finishes, the same inbox message is updated to show that pane.

Codex and Claude `Stop` hooks expose `last_assistant_message`; the status hooks
cache that exact response per pane. While an agent is still working—or until an
existing agent completes its first turn after installation—the bridge falls
back to the latest visible pane output.

The bridge only accepts a private chat from the configured Telegram user ID. It
does not offer shell execution: incoming text goes through a private tmux paste
buffer, and the pane ID is revalidated immediately before the paste.

### Set up the bot

1. In Telegram, use `@BotFather` to create a private bot and copy its token.
2. Install the local configuration and user service:

   ```bash
   ~/my-tmux/scripts/setup-telegram-bridge.sh
   ```

3. Edit `~/.config/tmux-telegram-bridge/env`, set
   `TMUX_TELEGRAM_BOT_TOKEN`, and start the service in safe pairing mode:

   ```bash
   systemctl --user enable --now tmux-telegram-bridge
   ```

4. Send `/id` to the bot. Put the returned user ID in
   `TMUX_TELEGRAM_ALLOWED_USER_ID`, then reload the private configuration:

   ```bash
   systemctl --user restart tmux-telegram-bridge
   ```

5. Send `/agents`. The bot now shows only the live agent panes detected by the
   same process-liveness rules as the sidebar and switcher.

The real token and user ID live outside this repository in a mode-`0600` file.
Never add them to Git. If this machine must keep the bridge running after every
login session closes, enable the user service manager persistently once:

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
