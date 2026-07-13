#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
README="$REPO_DIR/README.md"
deprecated_flag="codex""_hooks"

if grep -q "$deprecated_flag" "$README"; then
    echo "README should not document the deprecated Codex hooks feature flag" >&2
    exit 1
fi

grep -q 'hooks = true' "$README"
grep -q 'codex --enable hooks' "$README"
