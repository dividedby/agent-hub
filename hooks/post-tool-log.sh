#!/usr/bin/env bash
# PostToolUse hook: logs agent-hub route calls for usage auditing
# Claude Code passes JSON context on stdin

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null)

if [[ "$TOOL" == "Bash" ]]; then
  CMD=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null)
  if [[ "$CMD" == *"router.py route"* ]]; then
    LOG_DIR="$HOME/.claude/agent-hub"
    mkdir -p "$LOG_DIR"
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "{\"ts\":\"$TIMESTAMP\",\"cmd\":$(echo "$CMD" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")}" \
      >> "$LOG_DIR/route-log.jsonl"
  fi
fi
exit 0
