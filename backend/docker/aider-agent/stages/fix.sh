#!/usr/bin/env bash
# Stage 3: fix + verify. Applies the regression test from the reproduce stage
# as its own commit, lets the agent change application code (the test is
# read-only and restored if touched), then runs the full suite independently.
# Writes
#   fix.patch         the fix alone, on top of the regression-test commit
#   verification.md   before/after evidence for the PR and reviewer
#   outputs           {tests_passed, files_changed, attempts}
# The fix is left uncommitted in the working repo for the github_pr node.
# Exits non-zero unless the suite passes with a non-empty fix.
STAGE=fix
source /agent/stages/lib.sh

TITLE="$(python3 -c 'import json,os; p=os.environ.get("AGENT_INPUTS_FILE",""); print(((json.load(open(p)).get("incident") or {}).get("title") or "the reported bug") if os.path.isfile(p) else "the reported bug")' 2>/dev/null || echo 'the reported bug')"

PROTECTED=()
REPRO_PATCH="$(upstream_file repro.patch)"
if [ -n "$REPRO_PATCH" ] && [ -s "$REPRO_PATCH" ]; then
  git apply --index "$REPRO_PATCH" || die "could not apply the regression test patch"
  git "${GIT_ID[@]}" commit -q -m "${REPRO_COMMIT_MESSAGE:-test: reproduce $TITLE}"
  mapfile -t PROTECTED < <(git diff --name-only HEAD~1 HEAD)
  log "regression test committed: ${PROTECTED[*]}"
fi

run_setup

mapfile -t SOURCES < <(candidate_files)
ARGS=()
for f in "${SOURCES[@]}"; do ARGS+=("$f"); done
for f in "${PROTECTED[@]}"; do ARGS+=(--read "$f"); done
TRIAGE="$(upstream_file triage.md)"
REPORT="$(upstream_file repro_report.md)"

{
  incident_brief
  if [ -n "$TRIAGE" ]; then echo; echo "## Triage"; echo; cat "$TRIAGE"; fi
  if [ -n "$REPORT" ]; then echo; cat "$REPORT"; fi
  cat <<EOF

## Your task

Fix the root cause in the application code so the whole test suite passes.

Rules:
- Make the smallest correct change. No refactors, renames or unrelated cleanups.
- Do not modify or delete the regression test (${PROTECTED[*]:-none}); it defines correct behavior.
- The suite is run with: ${TEST_CMD}
EOF
} > /tmp/fix_prompt.md

tests_passed=false
attempts=0
for attempt in $(seq 1 $(( ${FIX_ATTEMPTS:-2} + 1 ))); do
  attempts=$attempt
  log "attempt $attempt: asking the agent for a fix"
  aider_run code /tmp/fix_prompt.md --test-cmd "$TEST_CMD" --auto-test "${ARGS[@]}"
  for f in "${PROTECTED[@]}"; do
    if ! git diff --quiet HEAD -- "$f" 2>/dev/null; then
      log "restoring protected regression test: $f"
      git checkout -q HEAD -- "$f"
    fi
  done

  if [ -z "$(changed_paths)" ]; then
    feedback="You made no changes. Edit the application code to fix the bug."
  elif run_tests "$OUT/test_report.txt"; then
    tests_passed=true
    break
  else
    feedback="The test suite still fails after your change:

\`\`\`
$(tail_file "$OUT/test_report.txt" 40)
\`\`\`"
  fi
  log "not fixed yet"
  printf '\n\n## Feedback on your previous attempt\n\n%s\n' "$feedback" >> /tmp/fix_prompt.md
done

[ -f "$OUT/test_report.txt" ] || run_tests "$OUT/test_report.txt" || true
export_patch "$OUT/fix.patch"
mapfile -t CHANGED < <(changed_paths)

{
  echo "# Verification"
  echo
  echo "- Test command: \`${TEST_CMD}\`"
  echo "- Regression test: ${PROTECTED[*]:-(none, the reproduce stage produced no test)}"
  echo "- Files changed by the fix: ${CHANGED[*]:-(none)}"
  echo "- Full suite after the fix: $($tests_passed && echo passing || echo FAILING)"
  echo
  echo "## Diff stat"
  echo
  echo '```'
  git apply --stat "$OUT/fix.patch" 2>/dev/null || true
  echo '```'
  echo
  echo "## Test run after the fix"
  echo
  echo '```'
  tail_file "$OUT/test_report.txt" 40
  echo '```'
} > "$OUT/verification.md"

changed_json="$(printf '%s\n' "${CHANGED[@]}" | python3 -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))')"
write_outputs "{\"tests_passed\": $tests_passed, \"files_changed\": $changed_json, \"attempts\": $attempts}"
log "tests passing: $tests_passed; changed: ${CHANGED[*]:-none}"

$tests_passed || die "the test suite does not pass with the proposed fix"
[ "${#CHANGED[@]}" -gt 0 ] || die "no fix was produced"
