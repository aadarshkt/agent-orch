#!/usr/bin/env bash
# Headless Aider: apply the node prompt to the mounted working clone,
# non-interactively, then exit. The commit is left to the git_commit node.
set -euo pipefail

PROMPT="$(cat "${AGENT_PROMPT_FILE:-}" 2>/dev/null || true)"
if [ -z "$PROMPT" ]; then
  PROMPT="${AIDER_MESSAGE:-Create a file NOTES.md describing this repository.}"
fi

mapfile -t REPOS < <(find "$AGENT_REPOS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null || true)
if [ "${#REPOS[@]}" -eq 0 ]; then
  echo "[aider] no repos mounted under $AGENT_REPOS_DIR" >&2
  exit 1
fi

cd "${REPOS[0]}"
echo "[aider] repo=${PWD}"
echo "[aider] prompt=${PROMPT:0:120}..."

MODEL_ARGS=()
if [ -n "${AIDER_MODEL:-}" ]; then
  MODEL_ARGS=(--model "$AIDER_MODEL")
fi

# --message: one-shot instruction; --yes: no confirmation prompts;
# --no-auto-commits: keep the diff in the tree for the git_commit node.
aider --message "$PROMPT" --yes --no-auto-commits --no-check-update "${MODEL_ARGS[@]}"
