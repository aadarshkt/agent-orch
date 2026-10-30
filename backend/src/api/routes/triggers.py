"""
Triggers API — alert webhooks that start workflow runs.

    POST /triggers/{workflow_id}/{source}     source: sentry | datadog | github | generic

The flow for one webhook:

    authenticate -> normalize -> match -> dedupe -> start run (incident as input)

Dedupe is per workflow and fingerprint. A repeat of an incident that already
has a run in flight, a PR open, or a run inside the dedupe window only bumps
its occurrence count; ``?force=true`` starts a fresh run regardless.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.routes.workflows import start_run
from src.db.models import IncidentModel, WorkflowModel
from src.db.session import get_db
from src.triggers.normalize import normalize
from src.triggers.security import WebhookAuthError, enrich_sentry, verify_webhook

router = APIRouter()

#: Incident statuses that suppress a new run no matter how old the last one is.
ACTIVE_STATUSES = frozenset({"running", "paused", "pr_open"})


def matches(incident: Dict[str, Any], match: Dict[str, Any]) -> bool:
    """Every match key must equal the incident field (or be contained in a list field).

    Dotted keys reach into nested fields, e.g. ``tags.env: production``.
    """
    from src.engine.templating import lookup

    for key, expected in (match or {}).items():
        actual = lookup(incident, key)
        allowed = expected if isinstance(expected, list) else [expected]
        allowed = [str(a) for a in allowed]
        if isinstance(actual, list):
            if not any(str(a) in allowed for a in actual):
                return False
        elif str(actual) not in allowed:
            return False
    return True


def select_trigger(
    triggers: List[Dict[str, Any]], source: str, incident: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """First trigger for `source` whose match rules accept the incident."""
    for trigger in triggers or []:
        if trigger.get("source") != source:
            continue
        if incident is None or matches(incident, trigger.get("match") or {}):
            return trigger
    return None


def should_dedupe(record: IncidentModel, window_hours: float, now: datetime) -> bool:
    if record.status in ACTIVE_STATUSES:
        return True
    last = record.last_run_at or record.first_seen
    return bool(last) and now - last < timedelta(hours=window_hours)


def _incident_row(record: IncidentModel) -> Dict[str, Any]:
    return {
        "id": record.id,
        "workflow_id": record.workflow_id,
        "source": record.source,
        "fingerprint": record.fingerprint,
        "title": record.title,
        "url": record.url,
        "status": record.status,
        "thread_id": record.thread_id,
        "pr_url": record.pr_url,
        "occurrences": record.occurrences,
        "runs": record.runs,
        "first_seen": record.first_seen.isoformat() if record.first_seen else None,
        "last_seen": record.last_seen.isoformat() if record.last_seen else None,
    }


def _prepare_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Background-side enrichment, kept out of the webhook's response time."""
    incident = inputs.get("incident")
    if isinstance(incident, dict) and incident.get("source") == "sentry":
        inputs = {**inputs, "incident": enrich_sentry(dict(incident))}
    return inputs


@router.post("/{workflow_id}/{source}")
async def receive_alert(
    workflow_id: str,
    source: str,
    request: Request,
    background_tasks: BackgroundTasks,
    force: bool = False,
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Receive an alert webhook and start (or dedupe) an issue-fix run."""
    workflow = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    candidates = [t for t in workflow.triggers or [] if t.get("source") == source]
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow '{workflow.name}' has no '{source}' trigger configured.",
        )

    body = await request.body()
    # Every trigger for one source shares a secret in practice; verify with the
    # first so an unauthenticated caller learns nothing from match failures.
    try:
        verify_webhook(source, candidates[0].get("secret_env"), request.headers, body, token)
    except WebhookAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Webhook body must be JSON.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object.")

    incident, reason = normalize(source, payload, dict(request.headers))
    if incident is None:
        return {"status": "ignored", "reason": reason}

    trigger = select_trigger(candidates, source, incident)
    if trigger is None:
        return {"status": "ignored", "reason": "no trigger match rule accepted this alert"}

    now = datetime.utcnow()
    record = (
        db.query(IncidentModel)
        .filter(
            IncidentModel.workflow_id == workflow.id,
            IncidentModel.fingerprint == incident["fingerprint"],
        )
        .first()
    )

    if record:
        record.occurrences = (record.occurrences or 0) + 1
        record.last_seen = now
        record.incident = incident
        if not force and should_dedupe(record, float(trigger.get("dedupe_window_hours", 24)), now):
            db.commit()
            return {"status": "deduped", "incident": _incident_row(record)}
        record.runs = (record.runs or 0) + 1
        record.last_run_at = now
        record.status = "running"
        record.pr_url = None
    else:
        record = IncidentModel(
            workflow_id=workflow.id,
            source=source,
            fingerprint=incident["fingerprint"],
            title=incident["title"],
            url=incident.get("url"),
            status="running",
            occurrences=1,
            runs=1,
            incident=incident,
            first_seen=now,
            last_seen=now,
            last_run_at=now,
        )
        db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent delivery of the same alert created the row first.
        db.rollback()
        return {"status": "deduped", "fingerprint": incident["fingerprint"]}

    inputs = {**(trigger.get("inputs") or {}), "incident": incident}
    thread_id = start_run(
        workflow, db, request, background_tasks, inputs=inputs, before_run=_prepare_inputs
    )
    record.thread_id = thread_id
    db.commit()
    return {"status": "started", "thread_id": thread_id, "incident": _incident_row(record)}


@router.get("/incidents")
def list_incidents(workflow_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Deduplicated incidents, newest activity first."""
    query = db.query(IncidentModel)
    if workflow_id:
        query = query.filter(IncidentModel.workflow_id == workflow_id)
    return [_incident_row(r) for r in query.order_by(IncidentModel.last_seen.desc()).all()]
