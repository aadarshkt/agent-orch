"""
Alert normalization — every source's webhook becomes one ``incident`` dict.

Downstream nodes (prompts, the PR body, the post-back) only ever read this
shape, so supporting a new alert source means adding one normalizer here.

    {
      "source":      "sentry" | "datadog" | "github" | "generic",
      "fingerprint": stable dedupe key (source-native id when there is one),
      "short_id":    short human id, safe for branch names,
      "title", "message", "level", "culprit", "url",
      "project", "environment", "release",
      "exception":   {"type", "value"},
      "frames":      [{"file", "function", "line", "context", "in_app"}],
      "stacktrace":  frames rendered as text (most recent call last),
      "logs":        raw log lines, if the alert carried any,
      "tags":        {key: value},
      "labels":      [str],
      "reply":       where to post the result back (source-specific ids),
    }

A normalizer returns ``None`` for events that should not start a run
(a resolved alert, a recovery notification, an unrelated issue action);
``ignored_reason`` explains why.
"""
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

Incident = Dict[str, Any]
Result = Tuple[Optional[Incident], str]


# ── shared helpers ───────────────────────────────────────────

_VOLATILE = re.compile(
    r"0x[0-9a-f]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+",
    re.IGNORECASE,
)


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def content_fingerprint(incident: Incident) -> str:
    """Fallback dedupe key when the source has no stable id of its own.

    The same bug fires with different ids, timestamps and amounts, so numbers
    and hex are stripped; the top in-app frame pins it to a code location.
    """
    exc = incident.get("exception") or {}
    frame = next(
        (f for f in reversed(incident.get("frames") or []) if f.get("in_app", True)),
        {},
    )
    signature = [
        exc.get("type") or "",
        _VOLATILE.sub("#", exc.get("value") or incident.get("title") or ""),
        frame.get("file") or "",
        frame.get("function") or "",
    ]
    return _hash(*signature)


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit].rstrip("-") or "incident"


def render_stacktrace(frames: List[Dict[str, Any]]) -> str:
    lines = ["Traceback (most recent call last):"] if frames else []
    for f in frames:
        loc = f'  File "{f.get("file") or "?"}", line {f.get("line") or "?"}, in {f.get("function") or "?"}'
        lines.append(loc)
        if f.get("context"):
            lines.append(f"    {str(f['context']).strip()}")
    return "\n".join(lines)


def _finish(incident: Incident, native_id: Optional[str] = None) -> Incident:
    """Fill the derived fields every normalizer shares."""
    incident.setdefault("frames", [])
    incident.setdefault("exception", {})
    incident.setdefault("tags", {})
    incident.setdefault("labels", [])
    incident.setdefault("reply", {})
    if incident["frames"] and not incident.get("stacktrace"):
        incident["stacktrace"] = render_stacktrace(incident["frames"])
    incident.setdefault("stacktrace", "")
    incident.setdefault("logs", "")
    incident["title"] = (incident.get("title") or "Untitled alert").strip()
    incident["fingerprint"] = (
        f"{incident['source']}:{native_id}" if native_id
        else f"{incident['source']}:{content_fingerprint(incident)}"
    )
    short = incident.get("short_id") or incident["fingerprint"].split(":", 1)[1][:10]
    incident["short_id"] = _slug(str(short), 24)
    incident["slug"] = f"{incident['short_id']}-{_slug(incident['title'], 32)}"
    return incident


# ── Sentry ───────────────────────────────────────────────────

def _sentry_frames(event: Dict[str, Any]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    values = ((event.get("exception") or {}).get("values")) or []
    if not values:
        # Legacy payloads nest interfaces under "sentry.interfaces.Exception".
        legacy = event.get("sentry.interfaces.Exception") or {}
        values = legacy.get("values") or []
    if not values:
        return {}, []
    exc = values[-1]  # Sentry orders chained exceptions oldest first.
    frames = []
    for f in ((exc.get("stacktrace") or {}).get("frames")) or []:
        frames.append({
            "file": f.get("filename") or f.get("abs_path") or f.get("module"),
            "function": f.get("function"),
            "line": f.get("lineno"),
            "context": f.get("context_line"),
            "in_app": f.get("in_app", True) is not False,
        })
    return {"type": exc.get("type"), "value": exc.get("value")}, frames


def _sentry_tags(event: Dict[str, Any]) -> Dict[str, str]:
    raw = event.get("tags") or []
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    tags = {}
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            tags[str(item[0])] = str(item[1])
        elif isinstance(item, dict) and "key" in item:
            tags[str(item["key"])] = str(item.get("value"))
    return tags


def normalize_sentry(payload: Dict[str, Any], resource: str = "") -> Result:
    """Sentry integration-platform webhooks (event_alert, issue) and legacy alerts."""
    data = payload.get("data") or {}
    action = payload.get("action")
    installation = (payload.get("installation") or {}).get("uuid")

    if "event" in data:  # resource: event_alert (an alert rule fired)
        event = data["event"]
        issue_id = str(event.get("issue_id") or event.get("group_id") or "")
        exc, frames = _sentry_frames(event)
        incident = {
            "source": "sentry",
            "title": event.get("title") or event.get("message"),
            "message": event.get("message") or exc.get("value") or "",
            "level": event.get("level"),
            "culprit": event.get("culprit"),
            "url": event.get("web_url") or event.get("issue_url"),
            "project": str(event.get("project") or ""),
            "environment": event.get("environment"),
            "release": event.get("release"),
            "exception": exc,
            "frames": frames,
            "tags": _sentry_tags(event),
            "rule": (data.get("triggered_rule") or ""),
            "reply": {"issue_id": issue_id, "installation_uuid": installation},
        }
        return _finish(incident, issue_id or None), ""

    if "issue" in data:  # resource: issue
        if action not in (None, "created", "unresolved"):
            return None, f"sentry issue action '{action}' does not start a run"
        issue = data["issue"]
        meta = issue.get("metadata") or {}
        issue_id = str(issue.get("id") or "")
        incident = {
            "source": "sentry",
            "short_id": issue.get("shortId"),
            "title": issue.get("title"),
            "message": meta.get("value") or "",
            "level": issue.get("level"),
            "culprit": issue.get("culprit"),
            "url": issue.get("permalink") or issue.get("web_url"),
            "project": ((issue.get("project") or {}).get("slug")) or "",
            "exception": {"type": meta.get("type"), "value": meta.get("value")},
            "frames": (
                [{"file": meta.get("filename"), "function": meta.get("function"),
                  "line": None, "context": None, "in_app": True}]
                if meta.get("filename") else []
            ),
            "occurrences": issue.get("count"),
            "reply": {"issue_id": issue_id, "installation_uuid": installation},
        }
        return _finish(incident, issue_id or None), ""

    if "event" in payload or "culprit" in payload:  # legacy "WebHooks" plugin
        event = payload.get("event") or {}
        issue_id = str(payload.get("id") or event.get("issue_id") or "")
        exc, frames = _sentry_frames(event)
        incident = {
            "source": "sentry",
            "title": payload.get("message") or event.get("title"),
            "message": payload.get("message") or "",
            "level": payload.get("level"),
            "culprit": payload.get("culprit"),
            "url": payload.get("url"),
            "project": payload.get("project") or payload.get("project_name") or "",
            "environment": event.get("environment"),
            "exception": exc,
            "frames": frames,
            "tags": _sentry_tags(event),
            "reply": {"issue_id": issue_id},
        }
        return _finish(incident, issue_id or None), ""

    return None, f"unrecognized sentry payload (resource '{resource}')"


# ── Datadog ──────────────────────────────────────────────────

_RECOVERED = {"recovered", "ok", "resolved", "no data recovered"}


def _datadog_tags(raw: Any) -> Dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    items = raw if isinstance(raw, list) else str(raw or "").split(",")
    tags = {}
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        key, _, value = item.partition(":")
        tags[key] = value
    return tags


def _logs_text(sample: Any) -> str:
    """Datadog's $LOGS_SAMPLE is JSON for log monitors; keep it readable."""
    if not sample:
        return ""
    if isinstance(sample, str):
        try:
            sample = json.loads(sample)
        except ValueError:
            return sample
    if isinstance(sample, dict):
        sample = sample.get("logs") or sample.get("samples") or [sample]
    lines = []
    for entry in sample if isinstance(sample, list) else [sample]:
        if isinstance(entry, dict):
            lines.append(str(entry.get("message") or entry.get("content") or json.dumps(entry)))
        else:
            lines.append(str(entry))
    return "\n".join(lines)


def normalize_datadog(payload: Dict[str, Any]) -> Result:
    """Datadog monitor webhooks (payload template documented in the guide)."""
    transition = str(payload.get("alert_transition") or payload.get("transition") or "").lower()
    if transition in _RECOVERED:
        return None, f"datadog transition '{transition}' does not start a run"

    message = str(payload.get("message") or payload.get("body") or "")
    exc, frames = _parse_python_traceback(message)
    tags = _datadog_tags(payload.get("tags"))
    native = payload.get("aggreg_key") or payload.get("alert_id") or payload.get("monitor_id")
    incident = {
        "source": "datadog",
        "short_id": f"dd-{native}" if native else None,
        "title": payload.get("title") or payload.get("event_title") or payload.get("alert_title"),
        "message": message,
        "level": payload.get("priority") or payload.get("alert_type"),
        "url": payload.get("link") or payload.get("url"),
        "project": tags.get("service") or payload.get("service") or "",
        "environment": tags.get("env"),
        "exception": exc,
        "frames": frames,
        "logs": _logs_text(payload.get("logs_sample")),
        "tags": tags,
        "reply": {
            "monitor_id": payload.get("alert_id") or payload.get("monitor_id"),
            "aggregation_key": payload.get("aggreg_key"),
        },
    }
    return _finish(incident, str(native) if native else None), ""


# ── GitHub issues ────────────────────────────────────────────

def normalize_github(payload: Dict[str, Any], event: str = "issues") -> Result:
    """A GitHub ``issues`` webhook (opened, or labeled) becomes an incident."""
    if event and event not in ("issues", ""):
        return None, f"github event '{event}' does not start a run"
    action = payload.get("action")
    if action not in ("opened", "labeled", "reopened"):
        return None, f"github issue action '{action}' does not start a run"
    issue = payload.get("issue") or {}
    if issue.get("pull_request"):
        return None, "pull request, not an issue"
    repo = payload.get("repository") or {}
    body = issue.get("body") or ""
    exc, frames = _parse_python_traceback(body)
    full_name = repo.get("full_name") or ""
    number = issue.get("number")
    incident = {
        "source": "github",
        "short_id": f"gh-{number}",
        "title": issue.get("title"),
        "message": body,
        "url": issue.get("html_url"),
        "project": full_name,
        "exception": exc,
        "frames": frames,
        "labels": [l.get("name") for l in issue.get("labels") or [] if l.get("name")],
        "reply": {"repo": full_name, "issue_number": number},
    }
    return _finish(incident, f"{full_name}#{number}"), ""


# ── generic (any tool, or a pasted log) ──────────────────────

def normalize_generic(payload: Dict[str, Any]) -> Result:
    """Anything with a title and/or logs: a curl from CI, a pasted traceback."""
    logs = payload.get("logs") or ""
    if isinstance(logs, list):
        logs = "\n".join(str(line) for line in logs)
    message = str(payload.get("message") or payload.get("description") or "")
    stack = str(payload.get("stacktrace") or "")
    exc, frames = _parse_python_traceback("\n".join([stack, logs, message]))
    title = payload.get("title") or (exc.get("type") and f"{exc['type']}: {exc.get('value') or ''}")
    if not title and not logs and not message:
        return None, "generic payload needs at least a title, message or logs"
    incident = {
        "source": "generic",
        "title": title or (message or logs).strip().splitlines()[0][:120],
        "message": message,
        "level": payload.get("level"),
        "url": payload.get("url"),
        "project": payload.get("project") or payload.get("service") or "",
        "environment": payload.get("environment"),
        "exception": exc,
        "frames": frames,
        "stacktrace": stack,
        "logs": str(logs),
        "tags": payload.get("tags") if isinstance(payload.get("tags"), dict) else {},
        "reply": {"callback_url": payload.get("callback_url")},
    }
    native = payload.get("fingerprint") or payload.get("id")
    return _finish(incident, str(native) if native else None), ""


# ── traceback parsing (alerts that only carry text) ──────────

_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_PY_EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)):\s?(.*)$")


def _parse_python_traceback(text: str) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Best-effort Python traceback extraction; other languages pass through as text."""
    frames: List[Dict[str, Any]] = []
    exc: Dict[str, str] = {}
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        m = _PY_FRAME.search(line)
        if m:
            context = lines[i + 1].strip() if i + 1 < len(lines) else None
            if context and _PY_FRAME.search(context):
                context = None
            frames.append({
                "file": m.group(1), "line": int(m.group(2)), "function": m.group(3),
                "context": context,
                "in_app": "site-packages" not in m.group(1) and "/lib/python" not in m.group(1),
            })
            continue
        e = _PY_EXC.match(line.strip())
        if e:
            exc = {"type": e.group(1), "value": e.group(2)}
    return exc, frames


NORMALIZERS = {
    "sentry": normalize_sentry,
    "datadog": normalize_datadog,
    "github": normalize_github,
    "generic": normalize_generic,
}


def normalize(source: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Result:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    if source == "sentry":
        return normalize_sentry(payload, headers.get("sentry-hook-resource", ""))
    if source == "github":
        return normalize_github(payload, headers.get("x-github-event", "issues"))
    if source in NORMALIZERS:
        return NORMALIZERS[source](payload)
    raise ValueError(f"Unknown alert source '{source}'. Supported: {sorted(NORMALIZERS)}")


# ── rendering for agents ─────────────────────────────────────

def incident_markdown(incident: Incident) -> str:
    """The incident as the markdown brief every agent prompt is given."""
    exc = incident.get("exception") or {}
    rows = [
        ("Source", incident.get("source")),
        ("Level", incident.get("level")),
        ("Project", incident.get("project")),
        ("Environment", incident.get("environment")),
        ("Release", incident.get("release")),
        ("Culprit", incident.get("culprit")),
        ("Link", incident.get("url")),
    ]
    out = [f"# Incident: {incident.get('title')}", ""]
    out += [f"- {k}: {v}" for k, v in rows if v]
    if exc.get("type") or exc.get("value"):
        out += ["", "## Exception", "", f"{exc.get('type') or 'Error'}: {exc.get('value') or ''}"]
    if incident.get("message") and incident.get("message") != exc.get("value"):
        out += ["", "## Message", "", str(incident["message"]).strip()]
    if incident.get("stacktrace"):
        out += ["", "## Stack trace", "", "```", incident["stacktrace"].strip(), "```"]
    if incident.get("logs"):
        logs = str(incident["logs"]).strip()
        out += ["", "## Logs", "", "```", logs[-8000:], "```"]
    if incident.get("tags"):
        out += ["", "## Tags", ""] + [f"- {k}: {v}" for k, v in incident["tags"].items()]
    return "\n".join(out).strip() + "\n"
