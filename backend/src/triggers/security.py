"""
Webhook authentication and enrichment.

Each trigger names the host env var holding its secret (``secret_env``); the
secret itself never lives in the workflow YAML.

  sentry   ``Sentry-Hook-Signature``: hex HMAC-SHA256 of the raw body, keyed by
           the integration's client secret.
  github   ``X-Hub-Signature-256``: ``sha256=<hex HMAC-SHA256>`` of the raw body.
  datadog  Datadog webhooks are unsigned, so a shared token is sent as a custom
  generic  header (``X-Agent-Orch-Token``) or a ``?token=`` query parameter.

A trigger without ``secret_env`` accepts unauthenticated calls, which is only
meant for local development.
"""
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional

from src.triggers.normalize import Incident, _finish, _sentry_frames, _sentry_tags


class WebhookAuthError(Exception):
    pass


def _hmac_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_webhook(
    source: str,
    secret_env: Optional[str],
    headers: Mapping[str, str],
    body: bytes,
    query_token: Optional[str] = None,
) -> None:
    """Raise WebhookAuthError unless the request carries the trigger's secret."""
    if not secret_env:
        return
    secret = os.getenv(secret_env, "")
    if not secret:
        raise WebhookAuthError(
            f"Trigger expects a secret in env '{secret_env}', but it is not set on the server."
        )
    headers = {k.lower(): v for k, v in headers.items()}

    if source == "sentry":
        given = headers.get("sentry-hook-signature", "")
        if not hmac.compare_digest(given, _hmac_hex(secret, body)):
            raise WebhookAuthError("Invalid Sentry-Hook-Signature.")
        return
    if source == "github":
        given = headers.get("x-hub-signature-256", "")
        if not hmac.compare_digest(given, f"sha256={_hmac_hex(secret, body)}"):
            raise WebhookAuthError("Invalid X-Hub-Signature-256.")
        return

    given = headers.get("x-agent-orch-token") or query_token or ""
    if not hmac.compare_digest(given, secret):
        raise WebhookAuthError("Missing or invalid X-Agent-Orch-Token.")


def enrich_sentry(incident: Incident) -> Incident:
    """Fetch the latest event's stack trace when the webhook carried none.

    Sentry's ``issue`` webhook has no frames. With ``SENTRY_AUTH_TOKEN`` set,
    the latest event is pulled so the agents see a real trace. Best effort: a
    failed lookup leaves the incident as it was.
    """
    token = os.getenv("SENTRY_AUTH_TOKEN", "")
    issue_id = (incident.get("reply") or {}).get("issue_id")
    if incident.get("source") != "sentry" or not token or not issue_id:
        return incident
    if len(incident.get("frames") or []) > 1:
        return incident
    base = os.getenv("SENTRY_BASE_URL", "https://sentry.io").rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/0/issues/{issue_id}/events/latest/",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            event: Dict[str, Any] = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        print(f"Sentry enrichment skipped for issue {issue_id}: {e}")
        return incident

    # Sentry's event API lists interfaces under "entries".
    for entry in event.get("entries") or []:
        if entry.get("type") == "exception":
            event.setdefault("exception", entry.get("data") or {})
    exc, frames = _sentry_frames(event)
    if frames:
        incident["frames"] = frames
        incident["stacktrace"] = ""
        incident["exception"] = exc or incident.get("exception") or {}
    incident["tags"] = {**_sentry_tags(event), **(incident.get("tags") or {})}
    incident.setdefault("environment", event.get("environment"))
    fingerprint = incident["fingerprint"]
    enriched = _finish(incident, None)
    enriched["fingerprint"] = fingerprint  # dedupe identity must not change
    return enriched
