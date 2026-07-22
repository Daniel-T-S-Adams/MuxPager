#!/usr/bin/env bash

# Terminal rendering helpers shared by the sidebar and focused tests.

fit_text_to_width() {
    local value="${1:-}"
    local width="${2:-0}"

    (( width > 0 )) || return 0
    printf '%s' "${value:0:width}"
}
