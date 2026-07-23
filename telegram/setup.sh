#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMCTL_BIN="${TMUX_AGENT_SYSTEMCTL_BIN:-systemctl}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-telegram-bridge"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="$CONFIG_DIR/env"
SERVICE_LINK="$SYSTEMD_DIR/tmux-telegram-bridge.service"

install -d -m 700 "$CONFIG_DIR"
install -d -m 700 "$SYSTEMD_DIR"
if [ ! -f "$ENV_FILE" ]; then
    install -m 600 "$PROJECT_ROOT/telegram/env.example" "$ENV_FILE"
fi
"$PROJECT_ROOT/scripts/setup-runtime-links.sh"
ln -sfn "$PROJECT_ROOT/telegram/tmux-telegram-bridge.service" "$SERVICE_LINK"

"$SYSTEMCTL_BIN" --user daemon-reload

echo "Telegram bridge service installed."
echo "1. Edit $ENV_FILE and set TMUX_TELEGRAM_BOT_TOKEN."
echo "2. Start pairing mode: systemctl --user enable --now tmux-telegram-bridge"
echo "3. Send /id to the bot, put that ID in the env file, then restart the service."
echo "4. Create a private Telegram group, enable Topics, and add the bot as an"
echo "   administrator with Manage Topics permission."
echo "5. Send /setup in the group's General topic."
if command -v loginctl >/dev/null 2>&1 && \
   [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true)" != "yes" ]; then
    echo "6. To keep it running after logout: sudo loginctl enable-linger \"$(id -un)\""
fi
