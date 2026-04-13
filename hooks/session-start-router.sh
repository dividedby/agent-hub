#!/usr/bin/env bash
# Claude Code SessionStart hook for agent-hub
# Runs when a new Claude Code session begins.
# stdout from SessionStart becomes part of Claude's initial context.

ROUTER="$HOME/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.5/skills/agent-hub/router.py"

if [ ! -f "$ROUTER" ]; then
  echo "[agent-hub] WARNING: router.py not found at $ROUTER — skill not active"
  exit 0
fi

echo "=== agent-hub: Free-Tier AI Router Active ==="
python3 "$ROUTER" status 2>/dev/null || echo "[agent-hub] Could not read usage status"
echo ""
echo "Route tasks with: python3 $ROUTER route \"<task>\" --type <code|complex|research|bulk|creative|fast|general>"
echo "============================================="
