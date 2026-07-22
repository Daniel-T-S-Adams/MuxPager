#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_HOME="$TMP_DIR/home"
FAKE_BIN="$TMP_DIR/bin"
STATUS_DIR="$TEST_HOME/.cache/tmux-agent-status"
PANE_DIR="$STATUS_DIR/panes"

mkdir -p "$FAKE_BIN" "$PANE_DIR"

tab=$'\t'
cat > "$FAKE_BIN/tmux" <<EOF
#!/usr/bin/env bash
set -euo pipefail

case "\${1:-}" in
    list-panes)
        if [ "\${2:-}" = "-a" ]; then
            printf 'repo${tab}%%1${tab}/repo${tab}100${tab}0${tab}codex${tab}codex${tab}\n'
            exit 0
        fi
        exit 1
        ;;
    show-option)
        echo off
        ;;
    *)
        exit 1
        ;;
esac
EOF
chmod +x "$FAKE_BIN/tmux"

cat > "$FAKE_BIN/ps" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

[ "$*" = "-eo pid=,ppid=,stat=,args=" ] || exit 1
cat <<'OUT'
100 1 S -bash
101 100 S /usr/local/bin/codex
OUT
EOF
chmod +x "$FAKE_BIN/ps"

export PATH="$FAKE_BIN:$PATH"
export HOME="$TEST_HOME"

# shellcheck source=../scripts/lib/session-status.sh
source "$REPO_DIR/scripts/lib/session-status.sh"
# shellcheck source=../scripts/lib/collect.sh
source "$REPO_DIR/scripts/lib/collect.sh"

declare -A KNOWN_AGENTS=()
declare -A LIVE_PANES=()
declare -A PID_PPID=()
declare -A PANE_COUNTS=()
ENTRIES=()
SEL_NAMES=()
SEL_TYPES=()
SESS_START=0
_COLLECT_TICK=0
_LAST_STATUS_MTIME=""
_COLLECT_CHANGED=0

printf 'codex' > "$PANE_DIR/repo_%1.agent"
printf 'working' > "$PANE_DIR/repo_%1.status"
printf 'working' > "$STATUS_DIR/repo.status"
printf 'same-second:first\n' > "$REFRESH_FILE"

set +e
collect_data
set -e
if [[ " ${ENTRIES[*]} " != *" S|repo|working|| "* ]]; then
    echo "Assertion failed: first collection should contain working state" >&2
    exit 1
fi

# Change only file contents and the refresh token. Directory mtimes and
# second-resolution timestamps may remain identical.
printf 'done' > "$PANE_DIR/repo_%1.status"
printf 'done' > "$STATUS_DIR/repo.status"
printf 'same-second:second\n' > "$REFRESH_FILE"

set +e
collect_data
set -e
if (( !_COLLECT_CHANGED )); then
    echo "Assertion failed: a new refresh token should force cache collection" >&2
    exit 1
fi
if [[ " ${ENTRIES[*]} " != *" S|repo|done|| "* ]]; then
    echo "Assertion failed: second collection should contain done state" >&2
    exit 1
fi

echo "sidebar refresh token regression checks passed"
