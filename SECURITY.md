# Security policy

## Supported versions

Security fixes are provided for the latest tagged release and the current
`main` branch.

## Reporting a vulnerability

Do not open a public issue containing a bot token, Telegram identifier, private
tmux output, or exploit details.

Use GitHub's private **Report a vulnerability** flow from the repository's
Security tab. If private reporting is unavailable, contact the maintainer
through the GitHub profile without including secrets in the initial message.

Include the affected version, relevant configuration with credentials redacted,
reproduction steps, and expected impact. Rotate an exposed Telegram token with
`@BotFather` before waiting for a response.

The implementation trust model and token-rotation procedure are documented in
[`docs/security.md`](docs/security.md).
