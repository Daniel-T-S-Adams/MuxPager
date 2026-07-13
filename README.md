# my-tmux

My customized, self-contained tmux setup. Clone it, symlink the config, done — no
plugin manager and no network fetch at startup.

## What's here

```
.tmux.conf                     # my tmux config (symlinked to ~/.tmux.conf)
scripts/agent-sidebar-ensure.sh# keeps the agent-status sidebar permanent
plugins/tmux-agent-status/     # VENDORED plugin, tracked via git subtree
```

The [tmux-agent-status](https://github.com/samleeney/tmux-agent-status) plugin is
vendored under `plugins/` with `git subtree`, so it lives directly in this repo and
is editable in place. There is no TPM — `.tmux.conf` sources the plugin's entry
file directly.

## Install on a new machine

```bash
git clone https://github.com/Daniel-T-S-Adams/my-tmux.git ~/my-tmux
ln -sfn ~/my-tmux/.tmux.conf ~/.tmux.conf
tmux kill-server 2>/dev/null; tmux            # start fresh so the config loads
```

> The config references `~/my-tmux` by absolute path. If you clone elsewhere,
> update the paths in `.tmux.conf` (the sidebar script itself is path-portable).

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
- **`?` icon for "asking"** — when Claude asks for permission or is waiting on
  your input (the `Notification` hook), the pane/session shows a red `?` instead
  of the green `✓`. Multi-agent session rows show a `?N` count. (Upstream mapped
  `Notification` to `done`; here it emits a distinct `ask` state — see
  `hooks/better-hook.sh`, `scripts/sidebar.sh`, `scripts/lib/collect.sh`.)
- **No sidebar INBOX** — `@agent-sidebar-inbox off` leaves just the SESSIONS tree.
