# Contributing

## Development setup

Clone the repository and run:

```bash
tests/run-all.sh
```

The suite requires the commands listed in the README. Installer tests use an
isolated temporary home and do not change the developer's real configuration.

Keep changes focused and include a regression test for behavioral fixes.
`git diff --check` must pass.

## Vendored core

`plugins/tmux-agent-status/` is a `git subtree`, not a submodule. Product-specific
changes may be made directly in that directory, but they should remain separate
from upstream refresh commits whenever possible.

To refresh the subtree:

```bash
git remote add upstream https://github.com/samleeney/tmux-agent-status.git
git subtree pull --prefix plugins/tmux-agent-status upstream main --squash
```

Resolve conflicts normally, preserve `THIRD_PARTY_NOTICES.md`, and rerun the
complete suite.

## Security-sensitive changes

Changes to Telegram authorization, topic mapping, tmux message delivery,
credential storage, or the systemd sandbox require:

- A test for unauthorized or stale targets.
- Confirmation that no message text is passed through a shell.
- `tests/test_no_secrets.py`.
- A complete `tests/run-all.sh` pass.
