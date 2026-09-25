#!/usr/bin/env bash
# Fake CLI agent. Reads the AGENT_* contract the executor injects and writes
# ai_review/report.md so the workflow can collect it as an artifact.
set -euo pipefail

echo "[test-agent] workspace = $AGENT_WORKSPACE"
echo "[test-agent] repos     = $AGENT_REPOS_DIR"
echo "[test-agent] skills    = $AGENT_SKILLS_DIR"
echo "[test-agent] context   = $AGENT_CONTEXT_DIR"

if [ -f "$AGENT_PROMPT_FILE" ]; then
  echo "[test-agent] prompt:"
  cat "$AGENT_PROMPT_FILE"
fi

echo "[test-agent] repos present:"
ls -la "$AGENT_REPOS_DIR" 2>/dev/null || echo "  (none)"

echo "[test-agent] upstream context:"
if [ -d "$AGENT_CONTEXT_DIR/upstream" ]; then
  find "$AGENT_CONTEXT_DIR/upstream" -type f | sed 's/^/  /'
else
  echo "  (none)"
fi

if [ -n "${TEST_AGENT_MESSAGE:-}" ]; then
  echo "[test-agent] message = $TEST_AGENT_MESSAGE"
fi

mkdir -p "$AGENT_WORKSPACE/ai_review"
{
  echo "# CLI Agent Test Report"
  echo
  echo "Message: ${TEST_AGENT_MESSAGE:-<none>}"
  echo "Workspace: $AGENT_WORKSPACE"
} > "$AGENT_WORKSPACE/ai_review/report.md"

echo "[test-agent] wrote $AGENT_WORKSPACE/ai_review/report.md"
