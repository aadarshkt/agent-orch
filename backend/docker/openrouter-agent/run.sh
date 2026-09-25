#!/usr/bin/env bash
# Minimal OpenRouter CLI agent. Reads the node prompt (and a short summary of
# any mounted repos), calls OpenRouter chat completions, and writes the reply
# to ai_review/report.md so the workflow can collect it as an artifact.
set -euo pipefail

MODEL="${OPENROUTER_MODEL:-nvidia/nemotron-3.5-lightning:free}"

PROMPT=""
if [ -n "${AGENT_PROMPT_FILE:-}" ] && [ -f "$AGENT_PROMPT_FILE" ]; then
  PROMPT="$(cat "$AGENT_PROMPT_FILE")"
fi
if [ -z "$PROMPT" ]; then
  PROMPT="${OPENROUTER_PROMPT:-Summarize what this workspace contains, in markdown.}"
fi

# Give the model a little context about mounted repos, if any.
CONTEXT=""
if [ -d "${AGENT_REPOS_DIR:-}" ]; then
  CONTEXT="$(cd "$AGENT_REPOS_DIR" && for d in */; do
    [ -d "$d" ] && printf -- '- %s: %s\n' "$d" "$(ls "$d" 2>/dev/null | head -n 20 | tr '\n' ' ')"
  done)"
fi

# Include any upstream artifacts handed off by previous nodes.
UPSTREAM=""
if [ -d "${AGENT_CONTEXT_DIR:-}/upstream" ]; then
  UPSTREAM="$(find "$AGENT_CONTEXT_DIR/upstream" -type f -size -100k 2>/dev/null | head -n 20 | while IFS= read -r f; do
    printf '\n--- %s ---\n' "$f"
    head -c 4000 "$f"
  done)"
fi

export OPENROUTER_MODEL="$MODEL"
export AGENT_PROMPT="$PROMPT"
export AGENT_CONTEXT_SUMMARY="$CONTEXT"
export AGENT_UPSTREAM="$UPSTREAM"

python3 - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

key = os.environ.get("OPENROUTER_API_KEY", "").strip()
if not key:
    sys.exit("OPENROUTER_API_KEY is not set")

model = os.environ["OPENROUTER_MODEL"]
content = os.environ["AGENT_PROMPT"]
context = os.environ.get("AGENT_CONTEXT_SUMMARY", "")
upstream = os.environ.get("AGENT_UPSTREAM", "")
if context.strip():
    content = f"{content}\n\nWorkspace context:\n{context}"
if upstream.strip():
    content = f"{content}\n\nUpstream artifacts from previous nodes:\n{upstream}"

body = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": content}],
    "max_tokens": 2048,
}).encode()

RETRYABLE = {429, 500, 502, 503, 504}

def call():
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)

data = None
for attempt in range(1, 4):
    try:
        data = call()
        break
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code in RETRYABLE and attempt < 3:
            wait = 5 * attempt
            print(f"[openrouter-agent] HTTP {e.code}, retrying in {wait}s ({attempt}/3): {detail[:120]}")
            time.sleep(wait)
            continue
        sys.exit(f"OpenRouter HTTP {e.code}: {detail}")

msg = data["choices"][0]["message"]
# Reasoning models may put the answer in `reasoning` and leave `content` null.
text = (msg.get("content") or msg.get("reasoning") or "").strip()
if not text:
    sys.exit("OpenRouter returned empty content")

usage = data.get("usage") or {}
out_dir = os.path.join(os.environ.get("AGENT_WORKSPACE", "/workspace"), "ai_review")
os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
    f.write(f"# OpenRouter agent report\n\nModel: {model}\n\n{text}\n")

print(f"[openrouter-agent] model={model} cost={usage.get('cost')} tokens={usage.get('total_tokens')}")
print(text)
PY

echo "[openrouter-agent] wrote ai_review/report.md"
