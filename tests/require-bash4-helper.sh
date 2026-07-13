#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$REPO_DIR/scripts/lib/require-bash4.sh"

require_bash4

bash4="$(_tmux_agent_status_find_bash4)"
[ -n "$bash4" ] || {
    echo "Expected to find a Bash 4+ executable" >&2
    exit 1
}

"$bash4" -c '[ "${BASH_VERSINFO[0]}" -ge 4 ]'

echo "Bash 4 requirement helper checks passed"
