"""
Alert Notify Executor.

Closes the loop: posts the run's outcome back where the alert came from, so
whoever is looking at the alert sees the draft PR (or why there is none)
without opening agent-orch.

  sentry   a comment on the issue (SENTRY_AUTH_TOKEN, SENTRY_BASE_URL), plus a
           linked external issue when the alert came through a Sentry
           integration installation
  datadog  an event sharing the alert's aggregation key and monitor tag, so it
           appears in the monitor's event stream (DD_API_KEY, DD_SITE)
  github   a comment on the originating issue (GITHUB_TOKEN)
  generic  a JSON POST to the incident's callback_url

Missing credentials are reported and skipped rather than failing the run: the
PR already exists, and a lost notification must not hide it.
"""
import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from src.engine.templating import first_output, node_outputs
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


def _first(artifacts: Dict[str, Any], suffix: str) -> Optional[Any]:
    return next((v for k, v in artifacts.items() if k.endswith(suffix) and v), None)


def _artifact_excerpt(artifacts: Dict[str, Any], filename: str, limit: int = 1200) -> str:
    for key, paths in artifacts.items():
        if not key.endswith("_artifacts") or not isinstance(paths, list):
            continue
        for path in paths:
            if os.path.basename(path) == filename and os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
                return text[:limit] + ("..." if len(text) > limit else "")
    return ""


def default_message(state: Dict[str, Any]) -> Tuple[str, str]:
    """(outcome, message) summarizing what the run produced."""
    artifacts = state.get("artifacts") or {}
    outputs = node_outputs(artifacts)
    pr_url = _first(artifacts, "_pr_url")
    if pr_url:
        parts = [f"agent-orch opened a draft PR with a proposed fix: {pr_url}"]
        root_cause = first_output(outputs, "root_cause")
        if root_cause:
            parts.append(f"Root cause: {root_cause}")
        if first_output(outputs, "reproduced"):
            parts.append("A regression test reproduces the bug and passes with the fix.")
        parts.append("It is a draft and needs a human review before merging.")
        return "pr_opened", "\n\n".join(parts)

    stopped = state.get("error") or "the run ended without a verified fix"
    parts = [f"agent-orch could not produce a verified fix. Stopped because: {stopped}"]
    postmortem = _artifact_excerpt(artifacts, "postmortem.md")
    if postmortem:
        parts.append(postmortem)
    elif first_output(outputs, "actionable") is False:
        summary = first_output(outputs, "summary") or ""
        parts.append(f"Triage judged this not fixable in code. {summary}".strip())
    return "no_fix", "\n\n".join(parts)


@register_executor("alert_notify")
class AlertNotifyExecutor(BaseExecutor):
    """Post the run outcome (PR link or failure summary) back to the alert's source."""

    runtime_kind = "none"
    display_name = "Alert Notify"

    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "title": "Message",
                "description": "Override the generated message. Supports {{ }} placeholders.",
            },
            "required": {
                "type": "boolean",
                "default": False,
                "title": "Fail the run if posting fails",
            },
        },
    }

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        node_id = node_config["id"]
        agent_name = node_config.get("agent_name", node_id)
        params = node_config.get("params", {})
        incident = (state.get("inputs") or {}).get("incident") or {}

        outcome, message = default_message(state)
        message = params.get("message") or message
        pr_url = _first(state.get("artifacts") or {}, "_pr_url")

        try:
            result = await asyncio.to_thread(
                self.post, incident, message, pr_url, outcome, context.thread_id
            )
            ok, error = True, None
        except Exception as e:
            result, ok, error = f"failed: {e}", False, str(e)
        await self._emit(context, f"[{agent_name}] Notify {incident.get('source') or 'none'}: {result}")

        artifacts = dict(state.get("artifacts", {}))
        artifacts[f"{node_id}_outputs"] = {"outcome": outcome, "result": result, "message": message}
        artifacts[f"{node_id}_output"] = message
        failed = not ok and params.get("required", False)
        # Notifying is the last step of either branch; it must not erase the
        # upstream failure that sent the run here.
        return {
            **state,
            "artifacts": artifacts,
            "current_step": node_id,
            "status": "failed" if failed else state.get("status") or "completed",
            "error": error if failed else state.get("error"),
        }

    # ── per-source delivery ──────────────────────────────────

    def post(self, incident: Dict[str, Any], message: str, pr_url: Optional[str],
             outcome: str, thread_id: str) -> str:
        source = incident.get("source")
        reply = incident.get("reply") or {}
        if source == "sentry":
            return self._sentry(reply, message, pr_url)
        if source == "datadog":
            return self._datadog(incident, reply, message, outcome)
        if source == "github":
            return self._github(reply, message)
        if source == "generic" and reply.get("callback_url"):
            _http("POST", reply["callback_url"], {
                "thread_id": thread_id, "outcome": outcome, "pr_url": pr_url,
                "message": message, "fingerprint": incident.get("fingerprint"),
            })
            return f"posted to {reply['callback_url']}"
        return "skipped: no reply target for this incident"

    def _sentry(self, reply: Dict[str, Any], message: str, pr_url: Optional[str]) -> str:
        token = os.getenv("SENTRY_AUTH_TOKEN", "")
        issue_id = reply.get("issue_id")
        if not token or not issue_id:
            return "skipped: SENTRY_AUTH_TOKEN or issue id missing"
        base = os.getenv("SENTRY_BASE_URL", "https://sentry.io").rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        _http("POST", f"{base}/api/0/issues/{issue_id}/comments/", {"text": message}, headers)
        done = f"commented on Sentry issue {issue_id}"
        installation = reply.get("installation_uuid")
        if pr_url and installation:
            m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
            try:
                _http("POST", f"{base}/api/0/sentry-app-installations/{installation}/external-issues/", {
                    "issueId": int(issue_id),
                    "webUrl": pr_url,
                    "project": m.group(1) if m else "github",
                    "identifier": f"#{m.group(2)}" if m else pr_url,
                }, headers)
                done += " and linked the PR"
            except Exception as e:
                done += f" (PR link skipped: {e})"
        return done

    def _datadog(self, incident: Dict[str, Any], reply: Dict[str, Any], message: str, outcome: str) -> str:
        key = os.getenv("DD_API_KEY", "")
        if not key:
            return "skipped: DD_API_KEY not set"
        site = os.getenv("DD_SITE", "datadoghq.com")
        tags = ["source:agent-orch", f"agent_orch_outcome:{outcome}"]
        if reply.get("monitor_id"):
            tags.append(f"monitor_id:{reply['monitor_id']}")
        payload = {
            "title": f"agent-orch: {incident.get('title')}"[:100],
            "text": message,
            "tags": tags,
            "alert_type": "info" if outcome == "pr_opened" else "warning",
            "source_type_name": "agent-orch",
        }
        if reply.get("aggregation_key"):
            payload["aggregation_key"] = reply["aggregation_key"]
        _http("POST", f"https://api.{site}/api/v1/events", payload, {"DD-API-KEY": key})
        return "posted a Datadog event on the monitor"

    def _github(self, reply: Dict[str, Any], message: str) -> str:
        token = os.getenv("GITHUB_TOKEN", "")
        repo, number = reply.get("repo"), reply.get("issue_number")
        if not token or not repo or not number:
            return "skipped: GITHUB_TOKEN or issue reference missing"
        _http("POST", f"https://api.github.com/repos/{repo}/issues/{number}/comments", {"body": message}, {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        return f"commented on {repo}#{number}"

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(context.thread_id, {"event": "agent", "data": data})


def _http(method: str, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "agent-orch", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')[:200]}") from None
