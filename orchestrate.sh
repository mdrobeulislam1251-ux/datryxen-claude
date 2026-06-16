#!/usr/bin/env bash
# Core orchestrator — headless Claude Code on the Max subscription (no API key).
# Runs claude -p from this dir so ./CLAUDE.md loads as the operating brain.
#
#   ./orchestrate.sh "your task"
#   echo "your task" | ./orchestrate.sh
#
# Env overrides:
#   MODEL=claude-opus-4-8           model (default: opus 4.8, 1M context)
#   PERM=bypassPermissions          permission mode (default; matches this box)
#   OUTPUT=text                     output format: text | json | stream-json
set -euo pipefail
cd "$(dirname "$0")"

MODEL="${MODEL:-claude-opus-4-8}"
PERM="${PERM:-bypassPermissions}"
OUTPUT="${OUTPUT:-text}"

# Task from args, else stdin.
TASK="${*:-}"
if [ -z "$TASK" ] && [ ! -t 0 ]; then TASK="$(cat)"; fi
if [ -z "$TASK" ]; then echo "usage: ./orchestrate.sh \"<task>\"  (or pipe via stdin)" >&2; exit 1; fi

# Guard: keep this subscription-backed. An exported API key would silently bill the API account.
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "WARN: ANTHROPIC_API_KEY is set — it shadows the subscription and bills the API account. Unset it for sub-backed runs." >&2
fi

ARGS=(-p "$TASK" --model "$MODEL" --permission-mode "$PERM" --output-format "$OUTPUT")

# Register per-brain sub-agents if present (one JSON file per brain under agents/).
if [ -f agents/agents.json ]; then
  ARGS+=(--agents "$(cat agents/agents.json)")
fi

# Project MCP fleet, if wired (kept out of git; see README).
if [ -f .mcp.json ]; then
  ARGS+=(--mcp-config .mcp.json)
fi

exec claude "${ARGS[@]}"
