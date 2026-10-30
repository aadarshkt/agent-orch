#!/usr/bin/env bash
# Stage 2: reproduce. The agent may only add or edit tests; the stage proves
# the new test FAILS on the current code. Writes
#   repro.patch       the regression test, as a patch on the base commit
#   repro_report.md   baseline vs. with-test results, for the PR and reviewer
#   outputs           {reproduced, baseline_passed, test_files, attempts}
# Exits non-zero when nothing reproduces and REQUIRE_REPRO is true (default),
# so the workflow can route to its failure branch instead of guessing a fix.
STAGE=reproduce
source /agent/stages/lib.sh

run_setup

baseline_passed=true
run_tests "$OUT/baseline_tests.txt" || baseline_passed=false
log "baseline suite passing: $baseline_passed"

mapfile -t SOURCES < <(candidate_files)
READ_ARGS=()
for f in "${SOURCES[@]}"; do READ_ARGS+=(--read "$f"); done  # source is read-only here
TRIAGE="$(upstream_file triage.md)"

{
  incident_brief
  if [ -n "$TRIAGE" ]; then echo; echo "## Triage"; echo; cat "$TRIAGE"; fi
  cat <<EOF

## Your task

Write a regression test that reproduces this bug. Follow the project's existing
test layout and style. The test must assert the CORRECT behavior, so it FAILS
on the current code and will pass once the bug is fixed.

Rules:
- Only add or edit test files. Do not change application code, and do not fix the bug.
- Keep it minimal: one focused test is ideal.
- The suite is run with: ${TEST_CMD}
EOF
} > /tmp/repro_prompt.md

reproduced=false
attempts=0
for attempt in $(seq 1 "${REPRO_ATTEMPTS:-2}"); do
  attempts=$attempt
  log "attempt $attempt: asking the agent for a failing test"
  aider_run code /tmp/repro_prompt.md "${READ_ARGS[@]}"
  revert_paths tests

  mapfile -t TESTS < <(changed_paths)
  if [ "${#TESTS[@]}" -eq 0 ]; then
    feedback="You did not add or change any test file. Add the regression test now."
  elif run_tests "$OUT/repro_tests.txt"; then
    feedback="The test you wrote PASSES on the current code, so it does not reproduce the bug. Make it assert the correct behavior described in the triage so it fails now."
  else
    reproduced=true
    break
  fi
  log "not reproduced: $feedback"
  printf '\n\n## Feedback on your previous attempt\n\n%s\n' "$feedback" >> /tmp/repro_prompt.md
done

mapfile -t TESTS < <(changed_paths)
export_patch "$OUT/repro.patch"

{
  echo "# Reproduction"
  echo
  if $reproduced; then
    echo "The regression test fails on the unfixed code, as it should."
  else
    echo "No failing test could be produced after $attempts attempt(s); the bug is NOT reproduced."
  fi
  echo
  echo "- Baseline suite (before the new test): $($baseline_passed && echo passing || echo FAILING)"
  echo "- Test files: ${TESTS[*]:-(none)}"
  echo "- Test command: \`${TEST_CMD}\`"
  if [ -f "$OUT/repro_tests.txt" ]; then
    echo
    echo "## Test run with the new test, before the fix"
    echo
    echo '```'
    tail_file "$OUT/repro_tests.txt" 60
    echo '```'
  fi
} > "$OUT/repro_report.md"

tests_json="$(printf '%s\n' "${TESTS[@]}" | python3 -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))')"
write_outputs "{\"reproduced\": $reproduced, \"baseline_passed\": $baseline_passed, \"test_files\": $tests_json, \"attempts\": $attempts}"
log "reproduced: $reproduced (tests: ${TESTS[*]:-none})"

if ! $reproduced && [ "${REQUIRE_REPRO:-true}" = true ]; then
  die "could not reproduce the bug with a failing test"
fi
