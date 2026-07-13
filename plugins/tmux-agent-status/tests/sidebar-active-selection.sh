#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=../scripts/lib/sidebar-selection.sh
source "$REPO_DIR/scripts/lib/sidebar-selection.sh"

assert_eq() {
    local actual="$1"
    local expected="$2"
    local message="$3"

    if [ "$actual" != "$expected" ]; then
        echo "Assertion failed: $message" >&2
        echo "Expected: $expected" >&2
        echo "Actual:   $actual" >&2
        exit 1
    fi
}

SEL_NAMES=("config" "c3" "config" "flows")
SEL_TYPES=("S" "S" "S" "S")
SESS_START=1
SEL_COUNT=${#SEL_NAMES[@]}

assert_eq \
    "$(sidebar_active_selection_index "config" "%8" "0")" \
    "2" \
    "active session should select the matching session row, not an inbox duplicate or the first session"

SEL_NAMES=("repo" "repo:w0" "repo:%1" "repo:w1" "other")
SEL_TYPES=("S" "P" "P" "P" "S")
SESS_START=0
SEL_COUNT=${#SEL_NAMES[@]}

assert_eq \
    "$(sidebar_active_selection_index "repo" "%1" "0")" \
    "2" \
    "active pane rows should win over enclosing window rows"

assert_eq \
    "$(sidebar_active_selection_index "repo" "%9" "1")" \
    "3" \
    "active window rows should win when the active pane has no row"

assert_eq \
    "$(sidebar_active_selection_index "repo" "%9" "5")" \
    "0" \
    "session row should be the fallback for the active session"

SEL_NAMES=("parent" "child-worktree")
SEL_TYPES=("S" "W")
SESS_START=0
SEL_COUNT=${#SEL_NAMES[@]}

assert_eq \
    "$(sidebar_active_selection_index "child-worktree" "%3" "0")" \
    "1" \
    "worktree session rows should be selectable active targets"

echo "sidebar active selection regression checks passed"
