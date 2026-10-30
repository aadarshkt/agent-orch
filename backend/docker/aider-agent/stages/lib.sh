#!/usr/bin/env bash
# Shared plumbing for the issue-fix stages (triage, reproduce, fix).
#
# The stages own the mechanics that must not depend on model quality: which
# files the agent may touch, running the tests, proving fail-before /
# pass-after, exporting patches and structured outputs. The *guidance* (what
# to look for, house style) comes from the node's YAML `instructions`, which
# the executor writes to $AGENT_PROMPT_FILE.
#
# Env contract (all optional unless noted):
#   AIDER_MODEL        model id, e.g. openrouter/<vendor>/<model> (required)
#   SETUP_CMD          install deps, run once per stage    e.g. pip install -e . pytest
#   TEST_CMD           run the test suite                  e.g. python -m pytest -q
#   TEST_TIMEOUT       seconds per test run (default 600)
#   TEST_PATH_REGEX    which paths count as tests (extended regex)
#   REQUIRE_REPRO      reproduce: fail the stage when no failing test (default true)
#   REPRO_ATTEMPTS     reproduce: agent attempts (default 2)
#   FIX_ATTEMPTS       fix: agent attempts after the first (default 2)
#   REPRO_COMMIT_MESSAGE  fix: message for the regression-test commit
set -euo pipefail

STAGE="${STAGE:-stage}"
OUT="${AGENT_WORKSPACE:-/workspace}"
UPSTREAM="${AGENT_CONTEXT_DIR:-/workspace/.agent/context}/upstream"
HISTORY=/tmp/aider.history.md
TEST_PATH_REGEX="${TEST_PATH_REGEX:-(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]+$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|(^|/)conftest\.py$}"
GIT_ID=(-c user.name=agent-orch -c user.email=agent@agent-orch)

log() { echo "[$STAGE] $*"; }
die() { echo "[$STAGE] ERROR: $*" >&2; exit 1; }

REPO_DIR="$(find "${AGENT_REPOS_DIR:-/workspace/repos}" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -n "$REPO_DIR" ] || die "no repo mounted under ${AGENT_REPOS_DIR:-/workspace/repos}"
[ -n "${AIDER_MODEL:-}" ] || die "AIDER_MODEL is not set"
cd "$REPO_DIR"

# Tool droppings never belong in a diff. info/exclude is local to the clone.
mkdir -p .git/info
printf '%s\n' '.aider*' '__pycache__/' '*.pyc' '.pytest_cache/' '.mypy_cache/' \
  '.ruff_cache/' '*.egg-info/' '.venv/' 'node_modules/' >> .git/info/exclude

# The brief every stage starts from: the incident plus the YAML guidance.
incident_brief() {
  if [ -f "${AGENT_INCIDENT_FILE:-}" ]; then
    cat "$AGENT_INCIDENT_FILE"
  else
    echo "# Incident"
    echo
    echo "No structured incident was provided; rely on the instructions below."
  fi
  if [ -s "${AGENT_PROMPT_FILE:-}" ]; then
    echo
    echo "## Instructions"
    echo
    cat "$AGENT_PROMPT_FILE"
  fi
}

upstream_file() {  # upstream_file <name> -> path of the newest upstream artifact with that name
  find "$UPSTREAM" -type f -name "$1" 2>/dev/null | head -1
}

run_setup() {
  [ -n "${SETUP_CMD:-}" ] || return 0
  log "setup: $SETUP_CMD"
  if ! bash -lc "$SETUP_CMD" > "$OUT/setup.log" 2>&1; then
    tail -n 40 "$OUT/setup.log"
    die "setup command failed (see setup.log)"
  fi
}

# run_tests <report-file>; returns the test command's exit code.
run_tests() {
  [ -n "${TEST_CMD:-}" ] || die "TEST_CMD is not set"
  local code=0
  timeout "${TEST_TIMEOUT:-600}" bash -lc "$TEST_CMD" > "$1" 2>&1 || code=$?
  return "$code"
}

# aider_run <ask|code> <message-file> [aider args...]
aider_run() {
  local mode="$1" message="$2"
  shift 2
  local chat_mode=code
  [ "$mode" = ask ] && chat_mode=ask
  rm -f "$HISTORY"
  aider --model "$AIDER_MODEL" --chat-mode "$chat_mode" --message-file "$message" \
    --yes-always --no-check-update --no-auto-commits --no-dirty-commits --no-gitignore \
    --no-pretty --no-stream --no-fancy-input --analytics-disable --no-show-model-warnings \
    --no-detect-urls --no-suggest-shell-commands \
    --chat-history-file "$HISTORY" --input-history-file /tmp/aider.input \
    "$@" || log "aider exited non-zero; checking what it left behind"
  rm -rf .aider.tags.cache* 2>/dev/null || true
}

last_reply() { python3 /agent/stages/helpers.py reply "$HISTORY"; }

# Paths changed vs HEAD, including new files.
changed_paths() { git status --porcelain --untracked-files=all | sed -E 's/^.{3}//; s/^.* -> //'; }
is_test_path() { echo "$1" | grep -Eq "$TEST_PATH_REGEX"; }

# Undo edits outside (or inside, with "tests") the allowed set.
revert_paths() {  # revert_paths <tests|non-tests>
  local keep="$1" path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if { [ "$keep" = tests ] && ! is_test_path "$path"; } || { [ "$keep" = non-tests ] && is_test_path "$path"; }; then
      log "reverting out-of-scope edit: $path"
      if git cat-file -e "HEAD:$path" 2>/dev/null; then
        git checkout -q HEAD -- "$path"
      else
        rm -f "$path"
      fi
    fi
  done < <(changed_paths)
}

export_patch() {  # export_patch <file>: working tree vs HEAD, new files included
  git add -A
  git diff --cached --binary > "$1"
  git reset -q
}

# Source files the incident and triage point at, as repo-relative paths.
candidate_files() { python3 /agent/stages/helpers.py files "$REPO_DIR" "${AGENT_INPUTS_FILE:-}" "$(upstream_file triage.json)"; }

write_outputs() {  # write_outputs <json>
  printf '%s\n' "$1" > "${AGENT_OUTPUTS_FILE:-$OUT/.agent/outputs.json}"
}

tail_file() { tail -n "${2:-60}" "$1" 2>/dev/null || true; }
