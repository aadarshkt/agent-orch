"""
Tests for the alert -> draft PR pipeline.

No network, no Docker, no real GitHub/Sentry/Datadog: covers param templating,
alert normalization and webhook auth, the trigger endpoint's dedupe, edge
routing on node outputs, the PR body and the push flow (against a local bare
repo), the post-back dispatch, and the stage scripts' parsers.
"""
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.db.session
from src.db.models import Base, IncidentModel
from src.engine.orchestrator import evaluate_condition
from src.engine.templating import render, template_context, first_output
from src.executors.alert_notify_executor import AlertNotifyExecutor, default_message
from src.executors.cli_agent_executor import _read_outputs, _write_run_inputs
from src.executors.github_pr_executor import (
    GitHubPrExecutor, build_pr_body, parse_github_repo, parse_verdict,
)
from src.registry.base_executor import ExecutionContext
from src.triggers.normalize import normalize
from src.triggers.security import WebhookAuthError, verify_webhook

BACKEND = Path(__file__).resolve().parent.parent
STAGES = BACKEND / "docker" / "aider-agent" / "stages"


@contextmanager
def env(**values):
    """Set (or with None, unset) env vars for the block, then restore them."""
    previous = {k: os.environ.get(k) for k in values}
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def sentry_event_alert(issue_id="4242", value="discount 30.00 exceeds 10.00"):
    return {
        "action": "triggered",
        "installation": {"uuid": "inst-1"},
        "data": {
            "triggered_rule": "Pricing errors",
            "event": {
                "issue_id": issue_id,
                "title": "PricingInvariantError: " + value,
                "level": "error",
                "culprit": "cart in total",
                "web_url": f"https://sentry.io/issues/{issue_id}/",
                "project": 7,
                "environment": "production",
                "tags": [["environment", "production"], ["release", "1.4.0"]],
                "exception": {"values": [{
                    "type": "PricingInvariantError",
                    "value": value,
                    "stacktrace": {"frames": [
                        {"filename": "app/checkout.py", "function": "checkout", "lineno": 12,
                         "context_line": "total = cart.total()", "in_app": True},
                        {"filename": "/srv/app/cart.py", "function": "discount", "lineno": 37,
                         "context_line": "total += self.subtotal() * rate", "in_app": True},
                    ]},
                }]},
            },
        },
    }


# ── templating ───────────────────────────────────────────────

class TestTemplating(unittest.TestCase):
    def test_whole_placeholder_keeps_type_and_embedded_is_stringified(self):
        ctx = {"repos": [{"repo": "r"}], "incident": {"title": "Boom", "n": 3}}
        self.assertEqual(render("{{ repos }}", ctx), [{"repo": "r"}])
        self.assertEqual(render("fix: {{ incident.title }} x{{incident.n}}", ctx), "fix: Boom x3")

    def test_missing_paths_render_empty_and_nested_structures_render(self):
        out = render({"a": ["{{ nope.deep }}", {"b": "{{ incident.title }}"}], "c": 5},
                     {"incident": {"title": "T"}})
        self.assertEqual(out, {"a": ["", {"b": "T"}], "c": 5})

    def test_host_env_syntax_is_untouched(self):
        self.assertEqual(render("${OPENROUTER_API_KEY}", {}), "${OPENROUTER_API_KEY}")

    def test_context_exposes_inputs_outputs_and_run(self):
        state = {"inputs": {"repo": "x"}, "artifacts": {"triage_outputs": {"summary": "s"}}}
        ctx = template_context(state, "t-1")
        self.assertEqual(render("{{ repo }}|{{ inputs.repo }}|{{ outputs.triage.summary }}|{{ run.thread_id }}", ctx),
                         "x|x|s|t-1")
        self.assertEqual(first_output(ctx["outputs"], "summary"), "s")


class TestEdgeConditions(unittest.TestCase):
    def test_routes_on_structured_outputs(self):
        cond = "status == 'completed' and outputs.get('triage', {}).get('actionable', True)"
        base = {"status": "completed", "error": None}
        self.assertTrue(evaluate_condition(cond, {**base, "artifacts": {"triage_outputs": {"actionable": True}}}))
        self.assertFalse(evaluate_condition(cond, {**base, "artifacts": {"triage_outputs": {"actionable": False}}}))
        # No outputs written: default to actionable rather than silently dropping the run.
        self.assertTrue(evaluate_condition(cond, {**base, "artifacts": {}}))


# ── normalization ────────────────────────────────────────────

class TestNormalize(unittest.TestCase):
    def test_sentry_event_alert(self):
        incident, _ = normalize("sentry", sentry_event_alert(), {"Sentry-Hook-Resource": "event_alert"})
        self.assertEqual(incident["fingerprint"], "sentry:4242")
        self.assertEqual(incident["exception"]["type"], "PricingInvariantError")
        self.assertEqual(incident["frames"][-1]["file"], "/srv/app/cart.py")
        self.assertIn('File "/srv/app/cart.py", line 37, in discount', incident["stacktrace"])
        self.assertEqual(incident["reply"], {"issue_id": "4242", "installation_uuid": "inst-1"})
        self.assertEqual(incident["tags"]["release"], "1.4.0")
        self.assertRegex(incident["slug"], r"^[a-z0-9-]+$")

    def test_sentry_resolved_issue_is_ignored(self):
        incident, reason = normalize("sentry", {"action": "resolved", "data": {"issue": {"id": "1"}}})
        self.assertIsNone(incident)
        self.assertIn("resolved", reason)

    def test_datadog_recovery_is_ignored_and_trigger_parses_traceback(self):
        self.assertIsNone(normalize("datadog", {"alert_transition": "Recovered", "title": "x"})[0])
        incident, _ = normalize("datadog", {
            "alert_transition": "Triggered",
            "title": "[Triggered] checkout errors",
            "aggreg_key": "abc123",
            "alert_id": "99",
            "tags": "env:prod,service:cart",
            "message": 'Traceback (most recent call last):\n  File "/app/cart.py", line 37, in discount\n'
                       "    total += x\nValueError: bad discount",
            "logs_sample": json.dumps([{"message": "ERROR checkout failed"}]),
        })
        self.assertEqual(incident["fingerprint"], "datadog:abc123")
        self.assertEqual(incident["exception"], {"type": "ValueError", "value": "bad discount"})
        self.assertEqual(incident["frames"][0]["function"], "discount")
        self.assertEqual(incident["project"], "cart")
        self.assertIn("checkout failed", incident["logs"])
        self.assertEqual(incident["reply"]["monitor_id"], "99")

    def test_github_issue(self):
        incident, _ = normalize("github", {
            "action": "labeled",
            "issue": {"number": 5, "title": "SAVE10 too generous", "body": "3 items got 30% off",
                      "html_url": "https://github.com/o/r/issues/5", "labels": [{"name": "agent-fix"}]},
            "repository": {"full_name": "o/r"},
        }, {"X-GitHub-Event": "issues"})
        self.assertEqual(incident["fingerprint"], "github:o/r#5")
        self.assertEqual(incident["labels"], ["agent-fix"])
        self.assertEqual(incident["reply"], {"repo": "o/r", "issue_number": 5})

    def test_generic_fingerprint_ignores_volatile_values(self):
        a, _ = normalize("generic", {"logs": 'File "cart.py", line 3, in total\nValueError: order 1001 bad'})
        b, _ = normalize("generic", {"logs": 'File "cart.py", line 3, in total\nValueError: order 2002 bad'})
        c, _ = normalize("generic", {"logs": 'File "cart.py", line 3, in other\nValueError: order 2002 bad'})
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertNotEqual(a["fingerprint"], c["fingerprint"])
        self.assertEqual(a["title"], "ValueError: order 1001 bad")

    def test_generic_requires_some_content(self):
        self.assertIsNone(normalize("generic", {})[0])


class TestWebhookAuth(unittest.TestCase):
    body = b'{"a": 1}'

    def test_sentry_signature(self):
        sig = hmac.new(b"s3cret", self.body, hashlib.sha256).hexdigest()
        with env(SENTRY_CLIENT_SECRET="s3cret"):
            verify_webhook("sentry", "SENTRY_CLIENT_SECRET", {"Sentry-Hook-Signature": sig}, self.body)
            with self.assertRaises(WebhookAuthError):
                verify_webhook("sentry", "SENTRY_CLIENT_SECRET", {"Sentry-Hook-Signature": "bad"}, self.body)

    def test_github_signature(self):
        sig = "sha256=" + hmac.new(b"gh", self.body, hashlib.sha256).hexdigest()
        with env(GH_SECRET="gh"):
            verify_webhook("github", "GH_SECRET", {"X-Hub-Signature-256": sig}, self.body)

    def test_token_header_or_query_and_unset_secret_is_refused(self):
        with env(TOK="t0k"):
            verify_webhook("datadog", "TOK", {"X-Agent-Orch-Token": "t0k"}, self.body)
            verify_webhook("generic", "TOK", {}, self.body, query_token="t0k")
            with self.assertRaises(WebhookAuthError):
                verify_webhook("generic", "TOK", {}, self.body)
        with env(TOK=None):
            with self.assertRaises(WebhookAuthError):
                verify_webhook("generic", "TOK", {"X-Agent-Orch-Token": ""}, self.body)


# ── trigger endpoint: import + dedupe ────────────────────────

class TestTriggerEndpoint(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db = self.SessionLocal()
        self.orig = src.db.session.SessionLocal
        src.db.session.SessionLocal = self.SessionLocal

        from src.config.parser import load_runtimes
        from src.registry.runtime_registry import register_runtimes
        register_runtimes(load_runtimes(str(BACKEND / "config" / "runtimes.yaml")))

        from src.registry.seed_node_types import _build_node_types
        from src.db.models import NodeTypeModel
        for nt in _build_node_types():
            self.db.add(NodeTypeModel(**nt))
        self.db.commit()

        self.client = self._client()
        resp = self.client.post("/workflows/import",
                                json={"yaml_path": str(BACKEND / "config" / "issue_fix.yaml")})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.workflow = resp.json()

    def tearDown(self):
        src.db.session.SessionLocal = self.orig
        self.db.close()

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes import triggers, workflows
        from src.db.session import get_db

        app = FastAPI()
        app.include_router(workflows.router, prefix="/workflows")
        app.include_router(triggers.router, prefix="/triggers")
        app.state.checkpointer = MemorySaver()

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    def _send_sentry(self, payload, secret="s3cret", force=False):
        body = json.dumps(payload).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        url = f"/triggers/{self.workflow['id']}/sentry" + ("?force=true" if force else "")
        return self.client.post(url, content=body, headers={
            "Content-Type": "application/json",
            "Sentry-Hook-Resource": "event_alert",
            "Sentry-Hook-Signature": sig,
        })

    def test_import_exposes_inputs_and_webhooks(self):
        self.assertEqual(self.workflow["inputs"]["test_command"], "python -m pytest -q")
        hooks = {t["source"]: t["webhook"] for t in self.workflow["triggers"]}
        self.assertEqual(hooks["sentry"], f"/triggers/{self.workflow['id']}/sentry")

    def test_alert_starts_one_run_and_repeats_are_deduped(self):
        with env(SENTRY_CLIENT_SECRET="s3cret", SENTRY_AUTH_TOKEN=None), \
                mock.patch("src.api.routes.workflows.run_workflow_sync") as run:
            first = self._send_sentry(sentry_event_alert())
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["status"], "started")
            run.assert_called_once()
            inputs = run.call_args.kwargs["inputs"]
            self.assertEqual(inputs["incident"]["fingerprint"], "sentry:4242")
            self.assertEqual(inputs["repo"], "https://github.com/aadarshkt/ao-demo-cart.git")

            again = self._send_sentry(sentry_event_alert(value="discount 45.00 exceeds 15.00"))
            self.assertEqual(again.json()["status"], "deduped")
            self.assertEqual(again.json()["incident"]["occurrences"], 2)
            self.assertEqual(run.call_count, 1)

            forced = self._send_sentry(sentry_event_alert(), force=True)
            self.assertEqual(forced.json()["status"], "started")
            self.assertEqual(forced.json()["incident"]["runs"], 2)
            self.assertEqual(run.call_count, 2)

        rows = self.db.query(IncidentModel).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].occurrences, 3)

    def test_failed_incident_reruns_after_window_but_open_pr_never_does(self):
        from datetime import datetime, timedelta
        from src.api.routes.triggers import should_dedupe

        now = datetime.utcnow()
        old = IncidentModel(status="failed", last_run_at=now - timedelta(hours=30))
        self.assertFalse(should_dedupe(old, 24, now))
        self.assertTrue(should_dedupe(IncidentModel(status="failed", last_run_at=now), 24, now))
        self.assertTrue(should_dedupe(IncidentModel(status="pr_open", last_run_at=now - timedelta(days=9)), 24, now))

    def test_bad_signature_and_ignored_events(self):
        with env(SENTRY_CLIENT_SECRET="s3cret"), mock.patch("src.api.routes.workflows.run_workflow_sync") as run:
            self.assertEqual(self._send_sentry(sentry_event_alert(), secret="wrong").status_code, 401)
            resolved = self._send_sentry({"action": "resolved", "data": {"issue": {"id": "1"}}})
            self.assertEqual(resolved.json()["status"], "ignored")
            run.assert_not_called()

    def test_github_match_rule_filters_unlabeled_issues(self):
        payload = {"action": "opened", "issue": {"number": 1, "title": "t", "labels": []},
                   "repository": {"full_name": "o/r"}}
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"gh", body, hashlib.sha256).hexdigest()
        with env(GITHUB_WEBHOOK_SECRET="gh"), mock.patch("src.api.routes.workflows.run_workflow_sync") as run:
            resp = self.client.post(f"/triggers/{self.workflow['id']}/github", content=body, headers={
                "Content-Type": "application/json", "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": sig,
            })
            self.assertEqual(resp.json()["status"], "ignored")
            run.assert_not_called()

    def test_execute_accepts_inputs_over_defaults(self):
        with mock.patch("src.api.routes.workflows.run_workflow_sync") as run:
            resp = self.client.post(f"/workflows/{self.workflow['id']}/execute",
                                    json={"inputs": {"ref": "release-1"}})
            self.assertEqual(resp.status_code, 200, resp.text)
            inputs = run.call_args.kwargs["inputs"]
            self.assertEqual(inputs["ref"], "release-1")
            self.assertEqual(inputs["test_command"], "python -m pytest -q")


# ── cli_agent run inputs / outputs ───────────────────────────

class TestCliAgentInputsOutputs(unittest.TestCase):
    def test_inputs_and_incident_are_written_and_outputs_read(self):
        tmp = tempfile.mkdtemp()
        try:
            incident, _ = normalize("sentry", sentry_event_alert())
            _write_run_inputs(tmp, {"repo": "r", "incident": incident})
            ctx = os.path.join(tmp, ".agent", "context")
            self.assertEqual(json.load(open(os.path.join(ctx, "inputs.json")))["repo"], "r")
            brief = open(os.path.join(ctx, "incident.md")).read()
            self.assertIn("PricingInvariantError", brief)
            self.assertIn("## Stack trace", brief)

            self.assertEqual(_read_outputs(tmp), {})
            with open(os.path.join(tmp, ".agent", "outputs.json"), "w") as f:
                f.write('{"reproduced": true}')
            self.assertEqual(_read_outputs(tmp), {"reproduced": True})
            with open(os.path.join(tmp, ".agent", "outputs.json"), "w") as f:
                f.write("[1, 2]")
            self.assertEqual(_read_outputs(tmp), {})
        finally:
            shutil.rmtree(tmp)


# ── github_pr ────────────────────────────────────────────────

class TestPrBody(unittest.TestCase):
    def _body(self, **overrides):
        incident, _ = normalize("sentry", sentry_event_alert())
        kwargs = dict(
            incident=incident,
            sections=[{"title": "Root cause analysis", "content": "The loop is wrong."},
                      {"title": "Verification", "content": "all green", "collapsed": True},
                      {"title": "Empty", "content": ""}],
            commits=[{"sha": "a" * 40, "subject": "test: reproduce"}, {"sha": "b" * 40, "subject": "fix: it"}],
            files=[{"path": "cart.py", "added": "1", "deleted": "3"}],
            branch="agent-orch/fix-x",
            test_command="python -m pytest -q",
            outputs={"triage": {"summary": "SAVE10 applies per item.", "root_cause": "loop in discount()"},
                     "reproduce": {"reproduced": True}, "fix": {"tests_passed": True}},
            review_text="Looks right.\nVERDICT: SHIP - fixes the root cause",
            run_ref="`t-1`",
        )
        kwargs.update(overrides)
        return build_pr_body(**kwargs)

    def test_reviewer_facts_come_first_then_commands_then_details(self):
        body = self._body()
        self.assertTrue(body.startswith("## Summary\n\nSAVE10 applies per item."))
        self.assertIn("| Root cause | loop in discount() |", body)
        self.assertIn("| Reproduced | Yes, the new test failed before the fix |", body)
        self.assertIn("| Automated review | SHIP (fixes the root cause) |", body)
        self.assertIn("1. `aaaaaaa` test: reproduce", body)
        self.assertIn("git checkout aaaaaaa   # regression test only: expect a failure", body)
        self.assertIn("- `cart.py` (+1 / -3)", body)
        self.assertIn("<details><summary>Verification</summary>", body)
        self.assertNotIn("## Empty", body)
        self.assertIn("<details><summary>Original stack trace / logs</summary>", body)
        self.assertLess(body.index("## How to review"), body.index("## Root cause analysis"))

    def test_body_has_no_emoji(self):
        self.assertTrue(all(ord(ch) < 0x2190 for ch in self._body()))

    def test_long_sections_are_truncated_under_github_limit(self):
        body = self._body(sections=[{"title": f"S{i}", "content": "x" * 50000} for i in range(6)])
        self.assertLessEqual(len(body), 61000)

    def test_parsers(self):
        self.assertEqual(parse_github_repo("https://github.com/o/r.git"), ("o", "r"))
        self.assertEqual(parse_github_repo("git@github.com:o/r.git"), ("o", "r"))
        self.assertEqual(parse_github_repo("https://x-access-token:t@github.com/o/r"), ("o", "r"))
        with self.assertRaises(ValueError):
            parse_github_repo("https://gitlab.com/o/r")
        self.assertEqual(parse_verdict("**VERDICT: DO NOT SHIP** - test is wrong"),
                         ("DO NOT SHIP", "test is wrong"))
        self.assertEqual(parse_verdict("nothing here"), (None, ""))


class _FakeApi:
    calls = []

    def __init__(self, token):
        self.token = token

    def open_or_update_pr(self, owner, name, **kw):
        _FakeApi.calls.append(("pr", owner, name, kw))
        return {"number": 7, "html_url": f"https://github.com/{owner}/{name}/pull/7", "draft": kw["draft"]}, "Opened"

    def try_request(self, method, path, payload=None):
        _FakeApi.calls.append((method, path, payload))


class TestGitHubPrFlow(unittest.TestCase):
    """Commit the fix on top of the regression-test commit and push both."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-pr-")
        self.remote = os.path.join(self.tmp, "remote.git")
        self.repo = os.path.join(self.tmp, "ws", "repos", "cart")
        git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
        self.git = git
        git("init", "-q", "--bare", self.remote)
        os.makedirs(self.repo)
        git("init", "-q", "-b", "main", cwd=self.repo)
        Path(self.repo, "cart.py").write_text("bug = True\n")
        git("add", "-A", cwd=self.repo)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo).strip()
        # What fix.sh leaves: a committed regression test + an uncommitted fix + junk.
        Path(self.repo, "test_cart.py").write_text("def test_x(): pass\n")
        git("add", "-A", cwd=self.repo)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "test: reproduce", cwd=self.repo)
        Path(self.repo, "cart.py").write_text("bug = False\n")
        Path(self.repo, ".aider.chat.history.md").write_text("junk")
        os.makedirs(os.path.join(self.repo, "__pycache__"))
        Path(self.repo, "__pycache__", "cart.cpython-312.pyc").write_text("junk")
        _FakeApi.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pushes_two_commits_without_junk_and_opens_draft(self):
        incident, _ = normalize("sentry", sentry_event_alert())
        state = {
            "inputs": {"incident": incident},
            "artifacts": {
                "fix_workspace": os.path.join(self.tmp, "ws"),
                "fix_repos": [{"name": "cart", "dir": self.repo, "ref": "main", "base_sha": self.base,
                               "repo": "https://github.com/acme/cart.git"}],
                "fix_outputs": {"tests_passed": True},
            },
            "status": "completed",
        }
        params = {"source_node": "fix", "branch": "agent-orch/fix-{{ incident.slug }}",
                  "title": "fix: thing", "labels": ["automated-fix"], "test_command": "pytest"}
        params = render(params, template_context(state))
        executor = GitHubPrExecutor()
        real_git = executor._git

        def git_with_local_remote(cwd, *args):
            if args and args[0] == "push":
                args = ("push", "--force", self.remote, args[-1])
            return real_git(cwd, *args)

        with env(GITHUB_TOKEN="tok"), \
                mock.patch("src.executors.github_pr_executor.GitHubApi", _FakeApi), \
                mock.patch.object(executor, "_git", side_effect=git_with_local_remote):
            import asyncio
            result = asyncio.run(executor.execute({"id": "open_pr", "params": params}, state,
                                                  ExecutionContext(thread_id="t-1")))

        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["artifacts"]["open_pr_pr_url"], "https://github.com/acme/cart/pull/7")
        branch = f"agent-orch/fix-{incident['slug']}"
        log = self.git("--git-dir", self.remote, "log", "--format=%s", branch).split("\n")
        self.assertEqual(log[:3], ["fix: thing", "test: reproduce", "base"])
        tree = self.git("--git-dir", self.remote, "ls-tree", "-r", "--name-only", branch).split()
        self.assertEqual(sorted(tree), ["cart.py", "test_cart.py"])

        kind, owner, name, kw = _FakeApi.calls[0]
        self.assertEqual((owner, name, kw["base"], kw["head"], kw["draft"]), ("acme", "cart", "main", branch, True))
        self.assertIn("2. `", kw["body"])
        self.assertIn(("POST", "/repos/acme/cart/issues/7/labels", {"labels": ["automated-fix"]}), _FakeApi.calls)

    def test_no_changes_fails_clearly(self):
        self.git("checkout", "-q", "--", "cart.py", cwd=self.repo)
        self.git("reset", "-q", "--hard", self.base, cwd=self.repo)
        state = {"artifacts": {"fix_repos": [{"name": "cart", "dir": self.repo, "ref": "main",
                                              "base_sha": self.base, "repo": "https://github.com/a/b"}]}}
        with env(GITHUB_TOKEN="tok"), mock.patch("src.executors.github_pr_executor.GitHubApi", _FakeApi):
            import asyncio
            result = asyncio.run(GitHubPrExecutor().execute(
                {"id": "open_pr", "params": {"source_node": "fix"}}, state, ExecutionContext(thread_id="t")))
        self.assertEqual(result["status"], "failed")
        self.assertIn("no changes", result["error"])


# ── alert_notify ─────────────────────────────────────────────

class TestAlertNotify(unittest.TestCase):
    def _run(self, state):
        import asyncio
        return asyncio.run(AlertNotifyExecutor().execute({"id": "notify", "params": {}}, state,
                                                         ExecutionContext(thread_id="t-1")))

    def test_posts_pr_link_to_sentry_and_links_external_issue(self):
        incident, _ = normalize("sentry", sentry_event_alert())
        state = {"inputs": {"incident": incident}, "status": "completed", "artifacts": {
            "open_pr_pr_url": "https://github.com/acme/cart/pull/7",
            "triage_outputs": {"root_cause": "per-item loop"},
            "reproduce_outputs": {"reproduced": True},
        }}
        with env(SENTRY_AUTH_TOKEN="st", SENTRY_BASE_URL="https://sentry.example"), \
                mock.patch("src.executors.alert_notify_executor._http") as http:
            result = self._run(state)
        urls = [c.args[1] for c in http.call_args_list]
        self.assertEqual(urls, ["https://sentry.example/api/0/issues/4242/comments/",
                                "https://sentry.example/api/0/sentry-app-installations/inst-1/external-issues/"])
        comment = http.call_args_list[0].args[2]["text"]
        self.assertIn("https://github.com/acme/cart/pull/7", comment)
        self.assertIn("per-item loop", comment)
        self.assertEqual(http.call_args_list[1].args[2]["identifier"], "#7")
        self.assertEqual(result["artifacts"]["notify_outputs"]["outcome"], "pr_opened")

    def test_datadog_event_and_missing_credentials_are_skipped_not_fatal(self):
        incident, _ = normalize("datadog", {"title": "t", "aggreg_key": "k", "alert_id": "9"})
        state = {"inputs": {"incident": incident}, "status": "completed", "artifacts": {}}
        with env(DD_API_KEY="dd", DD_SITE="datadoghq.eu"), \
                mock.patch("src.executors.alert_notify_executor._http") as http:
            self._run(state)
        url, payload = http.call_args.args[1], http.call_args.args[2]
        self.assertEqual(url, "https://api.datadoghq.eu/api/v1/events")
        self.assertEqual(payload["aggregation_key"], "k")
        self.assertIn("monitor_id:9", payload["tags"])
        with env(DD_API_KEY=None):
            result = self._run(state)
        self.assertIn("skipped", result["artifacts"]["notify_outputs"]["result"])
        self.assertEqual(result["status"], "completed")

    def test_failure_message_uses_postmortem(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "postmortem.md")
            Path(path).write_text("Reproduction failed: the test passed.")
            outcome, message = default_message({"artifacts": {"postmortem_artifacts": [path]}})
            self.assertEqual(outcome, "no_fix")
            self.assertIn("Reproduction failed", message)
        finally:
            shutil.rmtree(tmp)


# ── stage script parsers ─────────────────────────────────────

def _helpers():
    spec = importlib.util.spec_from_file_location("stage_helpers", STAGES / "helpers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStageHelpers(unittest.TestCase):
    def setUp(self):
        self.h = _helpers()
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "app"))
        Path(self.tmp, "app", "cart.py").write_text("")
        Path(self.tmp, "README.md").write_text("")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_last_reply_skips_user_turns_and_tool_output(self):
        history = os.path.join(self.tmp, "h.md")
        Path(history).write_text(
            "# aider chat started\n\n> Aider v0.86\n\n#### triage this\n\nfirst answer\n\n"
            "> Add app/cart.py to the chat? y\n\n#### I added these files\n\n## Summary\n\nreal answer\n\n> Tokens: 1k\n")
        self.assertEqual(self.h.last_reply(history), "## Summary\n\nreal answer")

    def test_triage_outputs(self):
        reply = ("## Summary\n\nSAVE10 is applied per item.\n\n## Root cause\n\n`cart.py` discount() loops.\n\n"
                 "## Proposed fix\n\nApply once.\n\nACTIONABLE: yes\nFILES: /srv/app/cart.py, missing.py")
        out = self.h.triage_outputs(reply, self.tmp)
        self.assertEqual(out, {"actionable": True, "summary": "SAVE10 is applied per item.",
                               "root_cause": "`cart.py` discount() loops.", "files": ["app/cart.py"]})
        self.assertFalse(self.h.triage_outputs("ACTIONABLE: no", self.tmp)["actionable"])

    def test_candidate_files_map_frames_onto_repo(self):
        inputs = os.path.join(self.tmp, "inputs.json")
        incident, _ = normalize("sentry", sentry_event_alert())
        Path(inputs).write_text(json.dumps({"incident": incident}))
        self.assertEqual(self.h.candidate_files(self.tmp, inputs), ["app/cart.py"])


if __name__ == "__main__":
    unittest.main()
