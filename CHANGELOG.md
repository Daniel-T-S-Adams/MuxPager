# Changelog

All notable changes to MuxPager are documented here. The project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `prefix + C` now provides an fzf repository picker, then creates an isolated
  `muxpager/<session>` Git branch and linked worktree in the selected checkout.
- Single-choice questions from Codex or Claude appear as inline buttons in the
  matching Telegram topic, with durable first-answer-wins delivery back to the
  exact agent pane.
- A shared local MCP question tool, automatic Codex/Claude registration,
  restart-safe choice storage, and direct-tmux-answer cancellation.
- A release-readiness fault register covering Telegram choice edge cases,
  stale agent states, approval prompts, long jobs, and broader hardening work.

### Changed

- Telegram agent topics now receive a human-readable title once and are never
  automatically renamed. Opaque conversation-ID suffixes are no longer shown,
  volatile tmux window names cannot cause title churn, and manual topic names
  are preserved.
- Telegram topics now follow the stable Codex or Claude conversation session
  ID instead of a tmux position. Resuming a conversation in another pane keeps
  its topic, while starting a new conversation in the same pane creates a new
  topic; legacy open topics migrate safely on the next hook event.
- Local prompt/final-message caches and choice requests are scoped to the same
  conversation identity, and ambiguous duplicate resumes pause delivery.
- Removed the timed manual Wait/snooze state and its `prefix + W`, sidebar, and
  switcher controls. Agents now remain at `?` until answered or can be parked
  indefinitely with `p`.
- Existing Wait markers are migrated to normal ready state when MuxPager is
  reloaded.
- Clarified the intended always-on server setup, reworded the feature list for
  first-time readers, and labeled Telegram setup steps by whether they happen in
  Telegram or in the terminal.

## [0.1.0] - 2026-07-23

### Added

- Self-contained tmux configuration with a vendored agent-status core.
- Permanent sidebar and hierarchical session/window/pane switcher.
- Hook-based Codex and Claude working/waiting state.
- Process-tree liveness validation that distinguishes agents from Bash panes.
- Private Telegram forum topics mapped to live agent panes.
- Safe Telegram-to-tmux message delivery and exact final-response notifications.
- Automatic Telegram topic close/reopen lifecycle.
- Relocatable, idempotent unified installer and conservative uninstaller.
- Machine diagnostics, documentation, and consolidated regression runner.

[Unreleased]: https://github.com/Daniel-T-S-Adams/MuxPager/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Daniel-T-S-Adams/MuxPager/releases/tag/v0.1.0
