# Architecture

MuxPager has one agent-state core and two first-class interfaces:
the local tmux UI and the private Telegram topic group.

```text
Codex hooks ──┐
              ├─► pane status/message cache ─► process-tree validation
Claude hooks ─┘                                  │
                                                ├─► permanent sidebar
                                                ├─► fzf switcher
                                                ├─► tmux status line
                                                └─► Telegram bridge

Codex/Claude MCP choice tool ─► durable choice store ─► Telegram buttons
                                                            │
                                                            └─► next user prompt
```

## Agent identity and state

Hooks report `working`, `done`, `ask`, or `parked` per pane. Persisted
hook state is accepted only when a live Codex or Claude process still exists in
that pane's process tree. Running, sleeping, and I/O-waiting processes remain
live; stopped, zombie, dead, and exited processes do not.

An agent's Telegram identity is:

```text
tmux session + window index + pane index + agent type
```

The temporary tmux pane ID is deliberately excluded. Recreating the same
logical pane therefore reopens the existing topic and retains its Telegram
history. Starting an agent in another position creates a distinct topic.

## Stable integration boundary

`bin/muxpager-agent-list` is the supported machine-readable boundary between the
core and external interfaces. It emits seven tab-separated fields:

```text
pane_id  session  window_index  window_name  pane_index  agent  status
```

Only validated, live agent panes appear. Consumers should not infer agent
presence from window names, cached hook files, or foreground process names
alone.

The Telegram bridge also reads the exact cached final message from:

```text
~/.cache/tmux-agent-status/panes/<session>_<pane-id>.last-message
```

## Runtime state

| State | Default location |
| --- | --- |
| Agent statuses and final messages | `~/.cache/tmux-agent-status/` |
| Telegram token and allowed user | `~/.config/tmux-telegram-bridge/env` |
| Telegram group/topic mapping | `~/.local/state/tmux-telegram-bridge/state.json` |
| Pending and completed choice questions | `~/.local/state/tmux-telegram-bridge/choices/` |
| Installer ownership metadata | `~/.config/muxpager/install.json` |

Credentials and learned chat IDs are never stored in the checkout.

## Telegram lifecycle

The bridge polls Telegram and reconciles tmux agent panes every few seconds.

1. A newly detected agent gets a topic.
2. A working agent remains mapped for the lifetime of its process, including
   time spent waiting for a child process.
3. An exited agent enters a configurable grace period.
4. After the grace period, the topic is closed rather than deleted.
5. The same logical agent returning reopens the old topic.
6. An incoming message is delivered only after the live pane mapping and
   `pane_dead` status are revalidated.

## Choice-question lifecycle

MuxPager registers one local MCP tool named `ask_choice` with Codex and Claude.
The tool accepts exactly one question and two to six labeled options, writes a
mode-`0600` request to the durable choice store, and returns immediately so the
agent can finish its turn. The Telegram bridge publishes inline buttons in the
matching topic.

The first accepted answer wins. A Telegram callback is bound to the configured
user, group, topic, message, request, and option before it is saved. The bridge
delivers the label as an ordinary next user prompt only after the pane is idle.
A direct tmux prompt submitted first marks the question answered locally.
Requests survive bridge restarts, and only one can remain active for a logical
agent topic.
