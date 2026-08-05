# Architecture

MuxPager has one agent-state core and two first-class interfaces:
the local tmux UI and the private Telegram topic group.

```text
Codex hooks ──┐
              ├─► pane status/conversation/prompt/message cache ─► process validation
Claude hooks ─┘                                         │
                                                       ├─► permanent sidebar
                                                       ├─► fzf switcher
                                                       ├─► tmux status line
                                                       └─► Telegram bridge

Codex/Claude MCP choice tool ─► durable choice store ─► Telegram buttons
                                                            │
                                                            └─► next user prompt

Telegram /new ─► bounded Git repository catalog ─► detached tmux agent
                                                        │
                                                        └─► conversation topic
```

## Agent identity and state

Hooks report `working`, `done`, `ask`, or `parked` per pane. Persisted
hook state is accepted only when a live Codex or Claude process still exists in
that pane's process tree. Running, sleeping, and I/O-waiting processes remain
live; stopped, zombie, dead, and exited processes do not.

Codex and Claude expose a stable `session_id` in their hook payloads. MuxPager's
Telegram identity is therefore:

```text
agent type + agent session_id
```

Tmux session, window, pane index, and pane ID are routing metadata only. Resuming
the same agent conversation anywhere reopens its existing topic and retains its
Telegram history. Starting a new conversation in the same pane creates a new
topic. If the same conversation is live in multiple panes simultaneously,
Telegram delivery pauses rather than choosing an ambiguous target.

An open topic created by an older MuxPager release is migrated once from its
position-based key when that live agent next reports a conversation ID. Closed
legacy topics are left as history and are never attached to a new conversation.
Live modern mappings also retain their validated working directory so they can
be resumed safely from Telegram after closing.

## Stable integration boundary

`bin/muxpager-agent-list` is the supported machine-readable boundary between the
core and external interfaces. It emits eight tab-separated fields:

```text
pane_id  session  window_index  window_name  pane_index  agent  status  conversation_id
```

Only validated, live agent panes appear. Consumers should not infer agent
presence from window names, cached hook files, or foreground process names
alone. The final field may be empty briefly for a process started before its
updated hook configuration loaded; the Telegram bridge pauses Codex/Claude
routing until it is populated.

The Telegram bridge reads the exact cached final message and queued local
prompts from conversation-scoped paths (the directory name is a SHA-256 digest
of agent type and session ID):

```text
~/.cache/tmux-agent-status/panes/conversations/<digest>.last-message
~/.cache/tmux-agent-status/panes/conversations/<digest>.prompts/*.prompt
~/.cache/tmux-agent-status/panes/<session>_<pane-id>.conversation
```

## Runtime state

| State | Default location |
| --- | --- |
| Agent statuses, local prompts, and final messages | `~/.cache/tmux-agent-status/` |
| Telegram token and allowed user | `~/.config/tmux-telegram-bridge/env` |
| Telegram group/topic mapping | `~/.local/state/tmux-telegram-bridge/state.json` |
| Pending and completed choice questions | `~/.local/state/tmux-telegram-bridge/choices/` |
| Pending Telegram agent launches | Telegram bridge `state.json` |
| Installer ownership metadata | `~/.config/muxpager/install.json` |
| Per-session Git worktrees | `<primary-checkout>/.muxpager/worktrees/` |

Credentials and learned chat IDs are never stored in the checkout.

## Isolated session creation

The `prefix + C` flow opens a popup repository picker. It discovers Git
checkouts near MuxPager while pruning managed `.muxpager/` directories, then
passes the selected repository and typed session name as separate arguments to
the worktree helper. MuxPager creates a unique `muxpager/<session>` branch at
the selected repository's committed `HEAD`, adds a linked worktree under its
primary checkout's `.muxpager/worktrees/` directory, and starts the detached
session there. It records the worktree path, branch, and primary checkout as
tmux session options.

Worktrees deliberately outlive their tmux sessions. Automatic removal could
destroy uncommitted changes, so cleanup remains an explicit Git operation.

## Prompt mirroring

Codex and Claude `UserPromptSubmit` hooks atomically queue each complete local
prompt with mode `0600`. Every live-pane reconciliation posts queued prompts to
the matching topic in order and removes each file only after Telegram accepts
it. Long prompts are split without dropping text. Before the bridge pastes a
Telegram message into tmux, it writes a short-lived SHA-256 origin marker;
the hook consumes that marker instead of queueing a duplicate.

## Telegram lifecycle

The bridge polls Telegram and reconciles tmux agent panes every few seconds.

1. A newly detected agent conversation gets a topic after its hook reports a
   session ID.
2. A working agent remains mapped for the lifetime of its process, including
   time spent waiting for a child process.
3. An exited agent enters a configurable grace period.
4. After the grace period, the topic is closed rather than deleted.
5. The same conversation returning, in any pane, reopens the old topic.
6. An incoming message is delivered only after the live pane mapping and
   `pane_dead` status are revalidated.

Closed position-based Codex/Claude topics from releases before conversation
identity are retained as history until the user sends `/prune legacy` in
Control and confirms the irreversible deletion. The bridge snapshots eligible
topics for that short-lived request, revalidates that none became active, calls
Telegram's topic-deletion API, and removes each local mapping only after the
topic is deleted or confirmed already missing.

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

## Telegram agent-launch lifecycle

`/new [SEARCH]` builds a cached, bounded catalog of Git working trees beneath
`TMUX_TELEGRAM_REPO_ROOT` (the service user's home directory by default). The
picker stores resolved candidate paths server-side; callback data contains only
a random launch ID and a result index. Repository selection is revalidated
inside the configured root before the bridge creates any process.

The bridge chooses an installed Codex or Claude executable, creates a detached
tmux session or window with the selected repository as its working directory,
and types the known absolute executable path literally into the new pane. A
launch remains in `starting` state until the normal pane collector supplies a
stable agent conversation ID and topic creation succeeds. Exited panes and
startup timeouts become visible failures in the original Control message.

## Telegram conversation-resume lifecycle

`/resume [SEARCH]` selects only closed, stable Codex/Claude conversation
identities that are not currently live. The bridge resolves their local agent
session metadata, validates the original working directory beneath
`TMUX_TELEGRAM_REPO_ROOT`, and stores only server-side candidates; Telegram
callback data contains a random request ID and bounded index.

After selection is revalidated, the bridge creates a new detached tmux session
dedicated to that conversation and types a command composed only from the known
agent executable, its fixed resume flag, and a locally stored UUID. The request
becomes ready only when the target pane reports the expected conversation ID
and the original Telegram topic has reopened. A different ID is a visible
failure rather than a handoff to the wrong topic.
