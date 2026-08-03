#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=../scripts/lib/collect.sh
source "$REPO_DIR/scripts/lib/collect.sh"

assert_eq() {
    local actual="$1" expected="$2" message="$3"
    if [ "$actual" != "$expected" ]; then
        printf 'Assertion failed: %s\nExpected: %s\nActual:   %s\n' \
            "$message" "$expected" "$actual" >&2
        exit 1
    fi
}

assert_eq \
    "$(_worktree_origin_paths '/srv/repo/.muxpager/worktrees/feature')" \
    $'/srv/repo\t/srv/repo' \
    "MuxPager worktree roots should map to the primary checkout root"

assert_eq \
    "$(_worktree_origin_paths '/srv/repo/.muxpager/worktrees/feature/src/api')" \
    $'/srv/repo/src/api\t/srv/repo' \
    "MuxPager worktree subdirectories should map to the matching source directory"

assert_eq \
    "$(_worktree_origin_paths '/srv/repo/.claude/worktrees/feature/tests')" \
    $'/srv/repo/tests\t/srv/repo' \
    "legacy Claude worktree subdirectories should remain supported"

if _worktree_origin_paths '/srv/repo/src' >/dev/null; then
    echo "Assertion failed: ordinary directories must not be treated as worktrees" >&2
    exit 1
fi

echo "worktree origin path checks passed"
