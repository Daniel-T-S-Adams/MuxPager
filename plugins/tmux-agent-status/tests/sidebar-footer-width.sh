#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=../scripts/lib/render-text.sh
source "$REPO_DIR/scripts/lib/render-text.sh"

footer=' ⏎ select  / search  w wait  p park  m mode  q quit'
fitted=$(fit_text_to_width "$footer" 42)

if (( ${#fitted} > 42 )); then
    echo "Assertion failed: sidebar footer must fit within the pane width" >&2
    exit 1
fi

if [ "$fitted" != "${footer:0:42}" ]; then
    echo "Assertion failed: footer fitting should preserve the visible prefix" >&2
    exit 1
fi

short=' type to filter  ⏎ select  esc cancel'
if [ "$(fit_text_to_width "$short" 42)" != "$short" ]; then
    echo "Assertion failed: fitting should leave short footer text unchanged" >&2
    exit 1
fi

if [ -n "$(fit_text_to_width "$footer" 0)" ]; then
    echo "Assertion failed: zero-width output should be empty" >&2
    exit 1
fi

if ! grep -Fq 'buf+="${RED}?${nd}${RST} "' "$REPO_DIR/scripts/sidebar.sh"; then
    echo "Assertion failed: waiting-agent header counts should use the red question mark" >&2
    exit 1
fi
if grep -Fq 'buf+="${BGRN}✓${nd}${RST} "' "$REPO_DIR/scripts/sidebar.sh"; then
    echo "Assertion failed: green ticks must remain exclusive to no-agent rows" >&2
    exit 1
fi

echo "sidebar footer width regression checks passed"
