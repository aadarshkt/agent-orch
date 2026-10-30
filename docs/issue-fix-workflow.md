# Alert to Draft PR

A production error in, a reviewable draft pull request out, and the link posted
back on the alert. The workflow lives in `backend/config/issue_fix.yaml`.

```
alert webhook
  -> POST /triggers/<workflow_id>/<source>   verify, normalize, match, dedupe
  -> triage ──(actionable)──> reproduce ──> fix ──> review ──> open_pr ──> notify
       ├──(not a code bug)─────────────────────────────────────────────────> notify
       └──(any stage fails)──> postmortem ─────────────────────────────────> notify
```

Nothing merges without a human: the output is always a draft PR.

## What each stage does

| Node | Runtime | Writes | Outputs | Fails when |
|---|---|---|---|---|
| triage | aider-agent, `stages/triage.sh` | `triage.md`, `triage.json` | `actionable`, `summary`, `root_cause`, `files` | the model returns nothing |
| reproduce | aider-agent, `stages/reproduce.sh` | `repro.patch`, `repro_report.md` | `reproduced`, `baseline_passed`, `test_files`, `attempts` | no failing test after `REPRO_ATTEMPTS` and `REQUIRE_REPRO=true` |
| fix | aider-agent, `stages/fix.sh` | `fix.patch`, `verification.md` | `tests_passed`, `files_changed`, `attempts` | the suite is still red, or the fix is empty |
| review | openrouter-agent | `review.md` | | |
| postmortem | openrouter-agent | `postmortem.md` | | |
| open_pr | `github_pr` executor | draft PR | `url`, `number`, `branch`, `action` | push or GitHub API error |
| notify | `alert_notify` executor | | `outcome`, `result` | never by default (`required: true` changes that) |

The stage scripts enforce the parts that must not depend on model quality:

- **triage** runs Aider in ask mode and resets the tree afterwards, so it cannot edit code.
- **reproduce** reverts any edit outside test paths (`TEST_PATH_REGEX`). It only
  succeeds if the new test fails on the unfixed code.
- **fix** commits the regression test first and passes it to Aider as read-only.
  If the test is edited anyway, it is restored. The full suite then runs
  independently of the agent.
- **open_pr** commits the fix on top of that test commit, so the PR has two
  commits: one test that proves the bug, then the change that fixes it.

Handoffs are files. Each node's artifacts are mounted for downstream nodes at
`.agent/context/upstream/<node>/`, and the normalized alert is at
`.agent/context/incident.md`.

## Setup

1. Build the images (`deploy/push-images.sh`, or locally):

   ```bash
   docker build -t agent-orch-aider:latest backend/docker/aider-agent
   ```

   The aider image carries Python and Node. A repo that needs another toolchain
   should use a runtime whose image extends it.

2. Set the following in `backend/.env`:

   | Variable | Needed for |
   |---|---|
   | `OPENROUTER_API_KEY` | all agent stages |
   | `GITHUB_TOKEN` | push + draft PR (contents and pull-request write on the target repo); GitHub issue comments |
   | `SENTRY_CLIENT_SECRET` | verifying Sentry webhooks |
   | `GITHUB_WEBHOOK_SECRET` | verifying GitHub webhooks |
   | `AGENT_ORCH_WEBHOOK_TOKEN` | Datadog and generic webhooks |
   | `SENTRY_AUTH_TOKEN`, `SENTRY_BASE_URL` | optional: pull the stack trace for Sentry issue alerts, comment back |
   | `DD_API_KEY`, `DD_SITE` | optional: post the result to the monitor's event stream |
   | `AGENT_ORCH_PUBLIC_URL` | optional: run links in the PR body |

   Each trigger names its secret's env var with `secret_env`. A trigger
   without one accepts unauthenticated calls, which is for local use only.

3. Import the workflow:

   ```bash
   curl -s localhost:8010/workflows/import -H 'content-type: application/json' \
     -d '{"yaml_path": "config/issue_fix.yaml"}'
   ```

   The response lists each trigger with its webhook path,
   `/triggers/<workflow_id>/<source>`.

## Pointing it at your repo

`workflow.inputs` holds the defaults for every run:

```yaml
inputs:
  repo: "https://github.com/acme/cart-api.git"
  ref: "main"
  setup_command: "pip install -e . pytest"
  test_command: "python -m pytest -q"
  model: "openrouter/<vendor>/<model>"
  review_model: "<vendor>/<model>"
```

A trigger can override any of them. That lets one workflow serve several
repos:

```yaml
triggers:
  - source: sentry
    secret_env: SENTRY_CLIENT_SECRET
    match: { project: "cart-api" }
    inputs: { repo: "https://github.com/acme/cart-api.git", test_command: "make test" }
  - source: sentry
    secret_env: SENTRY_CLIENT_SECRET
    match: { project: "billing" }
    inputs: { repo: "https://github.com/acme/billing.git" }
```

The first trigger whose `match` rules all hold is used. A rule's value can be a
list (any of), and dotted keys reach nested fields, for example
`tags.env: production`. Agent params reference inputs as `{{ name }}` and the
incident as `{{ incident.title }}`, `{{ incident.slug }}` and so on.

## Alert sources

### Sentry

Create an internal integration and set its webhook URL to
`https://<host>/triggers/<workflow_id>/sentry`. Put its client secret in
`SENTRY_CLIENT_SECRET`. Either of these starts a run:

- an alert rule whose action sends a notification to the integration
  (`event_alert`), which carries the full event and stack trace, or
- the `issue` webhook, for issues that are created or unresolved. These carry
  no stack trace, so set `SENTRY_AUTH_TOKEN` and the latest event's frames are
  fetched before the run starts.

### Datadog

Add a webhook integration with the URL
`https://<host>/triggers/<workflow_id>/datadog`. Add a custom header
`X-Agent-Orch-Token` set to `AGENT_ORCH_WEBHOOK_TOKEN`, and use this payload:

```json
{
  "alert_id": "$ALERT_ID",
  "aggreg_key": "$AGGREG_KEY",
  "alert_transition": "$ALERT_TRANSITION",
  "title": "$EVENT_TITLE",
  "message": "$EVENT_MSG",
  "priority": "$PRIORITY",
  "link": "$LINK",
  "tags": "$TAGS",
  "logs_sample": $LOGS_SAMPLE
}
```

Mention `@webhook-<name>` in the monitor. Recovery transitions are ignored.
A traceback in the monitor message or the log sample is parsed into frames.

### GitHub issues

Add a repository webhook for **Issues** events, with content type
`application/json`, URL `https://<host>/triggers/<workflow_id>/github`, and the
secret from `GITHUB_WEBHOOK_SECRET`. The default trigger only fires for issues
labeled `agent-fix` (`match: { labels: "agent-fix" }`), so not every new issue
starts a run.

### Generic (CI, scripts, a pasted log)

```bash
curl -s localhost:8010/triggers/<workflow_id>/generic \
  -H 'content-type: application/json' \
  -H "X-Agent-Orch-Token: $AGENT_ORCH_WEBHOOK_TOKEN" \
  -d '{
    "title": "SAVE10 gives far more than 10% off",
    "logs": "Traceback (most recent call last): ...",
    "callback_url": "https://ci.example.com/hooks/agent-orch"
  }'
```

At least one of `title`, `message` or `logs` is required. The optional fields
are `stacktrace`, `level`, `url`, `project`, `environment`, `tags`, and
`fingerprint` (a stable dedupe id). `callback_url` receives the outcome as a
JSON POST. The token can also go in `?token=` for tools that cannot set
headers.

## Dedupe

Incidents are keyed by fingerprint per workflow. The fingerprint is the
source's own id where one exists. Otherwise it is a hash of the exception type,
the message with numbers and ids stripped, and the top in-app frame. A repeat
alert only increments the occurrence count when:

- the incident has a run in flight, is paused, or has an open PR, or
- its last run started inside `dedupe_window_hours` (default 24).

Add `?force=true` to start a fresh run anyway. `GET /triggers/incidents`
(optionally `?workflow_id=`) lists incidents with their status, occurrence
count, run count and PR link.

## Responses

| `status` | Meaning |
|---|---|
| `started` | a run began; includes `thread_id` |
| `deduped` | counted against an existing incident, no new run |
| `ignored` | a valid event that should not start a run (resolved alert, no match); includes `reason` |

`401` means the signature or token is wrong, or the server is missing the
secret env var. `404` means there is no such workflow, or it has no trigger for
that source.

## Tuning the stages

These are set per agent under `env` in the YAML:

| Variable | Default | Effect |
|---|---|---|
| `SETUP_CMD` | none | installs dependencies once per stage |
| `TEST_CMD` | required | the suite; its exit code is the verdict |
| `TEST_TIMEOUT` | 600 | seconds per test run |
| `TEST_PATH_REGEX` | common test layouts | which paths count as tests |
| `REQUIRE_REPRO` | true | fail the run when no failing test can be written |
| `REPRO_ATTEMPTS` | 2 | attempts to write a failing test |
| `FIX_ATTEMPTS` | 2 | retries after the first fix attempt, each with the test output as feedback |

Guidance that is about style rather than mechanics, such as how to name tests
or where to look first, goes in the agent's `instructions`.

## Gating PRs on a human

A draft PR is already a review gate. To also require an approval in the
dashboard before anything is pushed, set `hitl_enabled: true` and add
`requires_approval: true` to the `open_pr` node. The run pauses before
`open_pr` and is decided with `POST /workflows/<thread_id>/review`.
