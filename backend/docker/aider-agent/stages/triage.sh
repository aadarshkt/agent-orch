#!/usr/bin/env bash
# Stage 1: triage. Read-only. Reads the incident and the code, and writes
#   triage.md    root cause analysis for humans (and the next stages)
#   triage.json  {actionable, summary, root_cause, files} -> node outputs
# Exit 0 even when the alert is not actionable: that is a valid answer,
# routed by the workflow's edge condition on outputs.actionable.
STAGE=triage
source /agent/stages/lib.sh

mapfile -t FILES < <(candidate_files)
log "files from the incident: ${FILES[*]:-(none)}"

{
  incident_brief
  cat <<'EOF'

## Your task

You are triaging the incident above in this repository. Do not edit any files.
Read the code involved and reply in markdown with exactly these sections:

## Summary
One or two sentences: what is broken, for whom, and why.

## Root cause
The file, function and faulty line(s), and why they are wrong.

## Proposed fix
The smallest change that fixes the root cause.

## Regression test
The test to add: where it goes, what it does, and what it asserts.

End with these two lines, exactly:
ACTIONABLE: yes   (or "no" if this is not a bug fixable in this repo's code,
                   e.g. infrastructure, configuration, or a third-party outage)
FILES: comma-separated repo-relative paths of the source files to change
EOF
} > /tmp/triage_prompt.md

aider_run ask /tmp/triage_prompt.md "${FILES[@]}"
last_reply > "$OUT/triage.md"
[ -s "$OUT/triage.md" ] || die "the model returned no triage"

python3 /agent/stages/helpers.py triage "$OUT/triage.md" "$REPO_DIR" > "$OUT/triage.json"
cp "$OUT/triage.json" "${AGENT_OUTPUTS_FILE:-$OUT/.agent/outputs.json}"
log "outputs: $(cat "$OUT/triage.json")"
git checkout -q -- . 2>/dev/null || true  # ask mode must leave the tree untouched
