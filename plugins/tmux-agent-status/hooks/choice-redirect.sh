#!/usr/bin/env bash

# Native terminal choice menus cannot be answered safely through Telegram.
# Exact PreToolUse matchers route the model to MuxPager's shared MCP tool.

cat >/dev/null 2>&1 || true

printf '%s\n' \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Use the MuxPager MCP ask_choice tool for this single-choice question, then end the turn. The user selection will arrive as the next user message."}}'
