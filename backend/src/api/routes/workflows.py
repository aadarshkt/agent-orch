"""
Workflows API — Full CRUD + execution for user-created workflows.

Workflows are stored in the database (no hardcoded YAML).
At execution time, the workflow's agent references are resolved from the DB,
each agent's node type is looked up, and the correct executor is dispatched.
"""
import uuid
import asyncio
import yaml
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.db.models import (
    WorkflowModel, AgentModel, NodeTypeModel, WorkflowExecutionModel, IncidentModel,
)
from src.config.schema import WorkflowConfig, NodeConfig, EdgeConfig, WorkflowSpec
from src.config.parser import load_workflow_spec, parse_workflow_spec
from src.engine.orchestrator import compile_workflow
from src.engine.templating import node_outputs
from src.engine.pubsub import event_bus
from src.registry.base_executor import ExecutionContext
from src.registry.runtime_registry import register_runtimes, resolve_runtime
from src.registry.executor_registry import list_executors

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

# Ensure executors are registered on import
import src.executors  # noqa: F401

router = APIRouter()


def _validate_agent_inputs(agent_name: str, executor_meta: dict, params: dict) -> None:
    """Validate an agent's params against its executor's input_schema."""
    schema = executor_meta.get("input_schema")
    if not schema or not HAS_JSONSCHEMA:
        return
    try:
        jsonschema.validate(instance=params, schema=schema)
    except jsonschema.ValidationError as e:
        loc = ".".join(str(p) for p in e.absolute_path) or "(root)"
        raise HTTPException(
            status_code=422,
            detail=f"Agent '{agent_name}': {e.message} (at {loc})",
        )


# ──────────────────────────────────────────────
# Request / Response schemas
# ──────────────────────────────────────────────

class WorkflowNodeInput(BaseModel):
    id: str
    agent_id: str
    requires_approval: bool = False


class WorkflowEdgeInput(BaseModel):
    from_node: str
    to_node: str
    condition: Optional[str] = None


class CreateWorkflowRequest(BaseModel):
    name: str
    description: Optional[str] = None
    hitl_enabled: bool = False
    nodes: List[WorkflowNodeInput]
    edges: List[WorkflowEdgeInput]


class UpdateWorkflowRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    hitl_enabled: Optional[bool] = None
    nodes: Optional[List[WorkflowNodeInput]] = None
    edges: Optional[List[WorkflowEdgeInput]] = None


class ImportWorkflowRequest(BaseModel):
    yaml_path: Optional[str] = None
    yaml_content: Optional[str] = None


class ExecuteWorkflowRequest(BaseModel):
    """Optional run inputs, merged over the workflow's declared defaults."""

    inputs: Dict[str, Any] = {}


class ReviewDecisionRequest(BaseModel):
    """A human's verdict on a run that is waiting at an approval gate.

    `decision` is recorded in the checkpointer rather than only in the
    execution row, so the outcome survives a page reload and is visible to
    whichever operator opens the run next.
    """

    decision: str  # "approve" | "reject"
    comment: Optional[str] = None
    reviewer: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.decision.strip().lower() in ("approve", "approved", "accept", "yes")


# ──────────────────────────────────────────────
# CRUD endpoints
# ──────────────────────────────────────────────

@router.post("/")
def create_workflow(req: CreateWorkflowRequest, db: Session = Depends(get_db)):
    """Create a new workflow definition."""
    existing = db.query(WorkflowModel).filter(WorkflowModel.name == req.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Workflow '{req.name}' already exists.")

    # Validate all agent_ids exist
    agent_ids = {n.agent_id for n in req.nodes}
    agents = db.query(AgentModel).filter(AgentModel.id.in_(agent_ids)).all()
    found_ids = {a.id for a in agents}
    missing = agent_ids - found_ids
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Agent(s) not found: {list(missing)}",
        )

    workflow = WorkflowModel(
        id=str(uuid.uuid4()),
        name=req.name,
        description=req.description,
        hitl_enabled=req.hitl_enabled,
        nodes=[n.model_dump() for n in req.nodes],
        edges=[e.model_dump() for e in req.edges],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)

    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "hitl_enabled": workflow.hitl_enabled,
        "nodes": workflow.nodes,
        "edges": workflow.edges,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
    }


def _import_workflow_spec(spec: WorkflowSpec, db: Session) -> WorkflowModel:
    """Validate and upsert a self-contained WorkflowSpec into agents + workflow."""
    # 1. Register embedded runtimes into the in-memory registry
    register_runtimes(spec.runtimes)

    # 2. Validate agent type + runtime references + inputs
    executors_by_key = {e["key"]: e for e in list_executors()}
    valid_executors = set(executors_by_key)
    for agent_name, agent_def in spec.agents.items():
        if agent_def.type not in valid_executors:
            raise HTTPException(
                status_code=422,
                detail=f"Agent '{agent_name}': unknown type '{agent_def.type}'. "
                       f"Available: {sorted(valid_executors)}",
            )
        if agent_def.runtime:
            try:
                resolve_runtime(agent_def.runtime)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"Agent '{agent_name}': {e}")

        params = dict(agent_def.inputs)
        if agent_def.runtime:
            params["runtime"] = agent_def.runtime
        _validate_agent_inputs(agent_name, executors_by_key[agent_def.type], params)

    # 3. Validate node -> agent references
    node_ids = {n.id for n in spec.workflow.nodes}
    for node in spec.workflow.nodes:
        if node.agent not in spec.agents:
            raise HTTPException(
                status_code=422,
                detail=f"Node '{node.id}' references unknown agent '{node.agent}'.",
            )

    # 4. Validate git_commit source_nodes reference real nodes
    for node in spec.workflow.nodes:
        agent_def = spec.agents[node.agent]
        if agent_def.type == "git_commit":
            for src in agent_def.inputs.get("source_nodes") or []:
                if src not in node_ids:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Agent '{node.agent}': source_nodes entry '{src}' "
                               f"not found in workflow nodes.",
                    )

    # 5. Upsert agents
    agents_by_name: Dict[str, AgentModel] = {}
    for agent_name, agent_def in spec.agents.items():
        params = dict(agent_def.inputs)
        if agent_def.runtime:
            params["runtime"] = agent_def.runtime

        agent = db.query(AgentModel).filter(AgentModel.name == agent_name).first()
        if agent:
            agent.node_type_key = agent_def.type
            agent.params = params
        else:
            agent = AgentModel(
                id=str(uuid.uuid4()),
                name=agent_name,
                node_type_key=agent_def.type,
                params=params,
            )
            db.add(agent)
        agents_by_name[agent_name] = agent
    db.flush()

    # 6. Build nodes
    nodes = [
        {
            "id": node.id,
            "agent_id": agents_by_name[node.agent].id,
            "requires_approval": node.requires_approval,
        }
        for node in spec.workflow.nodes
    ]

    edges = [
        {"from_node": e.from_node, "to_node": e.to_node, "condition": e.condition}
        for e in spec.workflow.edges
    ]

    # 7. Upsert workflow
    workflow = (
        db.query(WorkflowModel)
        .filter(WorkflowModel.name == spec.workflow.name)
        .first()
    )
    inputs = dict(spec.workflow.inputs)
    triggers = [t.model_dump() for t in spec.workflow.triggers]
    if workflow:
        workflow.description = spec.workflow.description
        workflow.hitl_enabled = spec.workflow.hitl_enabled
        workflow.nodes = nodes
        workflow.edges = edges
        workflow.inputs = inputs
        workflow.triggers = triggers
        workflow.updated_at = datetime.utcnow()
    else:
        workflow = WorkflowModel(
            id=str(uuid.uuid4()),
            name=spec.workflow.name,
            description=spec.workflow.description,
            hitl_enabled=spec.workflow.hitl_enabled,
            nodes=nodes,
            edges=edges,
            inputs=inputs,
            triggers=triggers,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(workflow)

    db.commit()
    db.refresh(workflow)
    return workflow


@router.post("/import")
def import_workflow(req: ImportWorkflowRequest, db: Session = Depends(get_db)):
    """Import a self-contained workflow YAML (path or inline content)."""
    if req.yaml_content:
        try:
            data = yaml.safe_load(req.yaml_content)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
        if not data:
            raise HTTPException(status_code=400, detail="YAML content is empty.")
        spec = parse_workflow_spec(data)
    elif req.yaml_path:
        try:
            spec = load_workflow_spec(req.yaml_path)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        raise HTTPException(status_code=400, detail="Provide yaml_path or yaml_content.")

    workflow = _import_workflow_spec(spec, db)
    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": "ready",
        "nodes": workflow.nodes,
        "edges": workflow.edges,
        "inputs": workflow.inputs or {},
        "triggers": [
            {**t, "webhook": f"/triggers/{workflow.id}/{t['source']}"}
            for t in workflow.triggers or []
        ],
    }


@router.get("/")
def list_workflows(db: Session = Depends(get_db)):
    """List all saved workflows."""
    workflows = db.query(WorkflowModel).all()
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "hitl_enabled": w.hitl_enabled,
            "node_count": len(w.nodes) if w.nodes else 0,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        }
        for w in workflows
    ]


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db)):
    """Get a workflow's full details."""
    w = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "hitl_enabled": w.hitl_enabled,
        "nodes": w.nodes,
        "edges": w.edges,
        "inputs": w.inputs or {},
        "triggers": w.triggers or [],
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


@router.put("/{workflow_id}")
def update_workflow(
    workflow_id: str, req: UpdateWorkflowRequest, db: Session = Depends(get_db)
):
    """Update a workflow definition."""
    w = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    if req.name is not None:
        existing = (
            db.query(WorkflowModel)
            .filter(WorkflowModel.name == req.name, WorkflowModel.id != workflow_id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail=f"Workflow '{req.name}' already exists.")
        w.name = req.name
    if req.description is not None:
        w.description = req.description
    if req.hitl_enabled is not None:
        w.hitl_enabled = req.hitl_enabled
    if req.nodes is not None:
        w.nodes = [n.model_dump() for n in req.nodes]
    if req.edges is not None:
        w.edges = [e.model_dump() for e in req.edges]

    w.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(w)

    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "hitl_enabled": w.hitl_enabled,
        "nodes": w.nodes,
        "edges": w.edges,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: str, db: Session = Depends(get_db)):
    """Delete a workflow."""
    w = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    db.delete(w)
    db.commit()
    return {"status": "deleted", "id": workflow_id}


# ──────────────────────────────────────────────
# Execution endpoints
# ──────────────────────────────────────────────

def _resolve_workflow(workflow_id: str, db: Session):
    """
    Load a workflow from DB and resolve all agent/node-type references
    into in-memory dicts for the orchestrator.
    """
    workflow = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    # Resolve agents
    agent_ids = {n["agent_id"] for n in workflow.nodes}
    agents = db.query(AgentModel).filter(AgentModel.id.in_(agent_ids)).all()
    agents_map = {
        a.id: {
            "name": a.name,
            "node_type_key": a.node_type_key,
            "params": a.params,
        }
        for a in agents
    }

    missing_agents = agent_ids - set(agents_map.keys())
    if missing_agents:
        raise HTTPException(
            status_code=422,
            detail=f"Agent(s) not found: {list(missing_agents)}",
        )

    # Resolve node types
    type_keys = {a["node_type_key"] for a in agents_map.values()}
    node_types = db.query(NodeTypeModel).filter(NodeTypeModel.type_key.in_(type_keys)).all()
    node_types_map = {
        nt.type_key: {
            "executor_key": nt.executor_key,
            "config_schema": nt.config_schema,
        }
        for nt in node_types
    }

    missing_types = type_keys - set(node_types_map.keys())
    if missing_types:
        raise HTTPException(
            status_code=422,
            detail=f"Node type(s) not found: {list(missing_types)}",
        )

    # Build WorkflowConfig from DB data
    config = WorkflowConfig(
        name=workflow.name,
        description=workflow.description,
        hitl_enabled=workflow.hitl_enabled,
        nodes=[
            NodeConfig(id=n["id"], agent_id=n["agent_id"], requires_approval=n.get("requires_approval", False))
            for n in workflow.nodes
        ],
        edges=[
            EdgeConfig(
                **{
                    "from": e.get("from_node", e.get("from")),
                    "to": e.get("to_node", e.get("to")),
                    "condition": e.get("condition"),
                }
            )
            for e in workflow.edges
        ],
    )

    return config, agents_map, node_types_map


def _claim_paused_execution(thread_id: str) -> bool:
    """Atomically move a run out of `paused`, returning False if someone else won.

    The review gate is a one-shot transition. A read-then-write pair would let a
    double-clicked Approve queue the same graph twice, so the guard lives in the
    UPDATE's WHERE clause where the database arbitrates.
    """
    from src.db.session import SessionLocal

    db_session = SessionLocal()
    try:
        claimed = (
            db_session.query(WorkflowExecutionModel)
            .filter(
                WorkflowExecutionModel.thread_id == thread_id,
                WorkflowExecutionModel.status == "paused",
            )
            .update(
                {WorkflowExecutionModel.status: "running"},
                synchronize_session=False,
            )
        )
        db_session.commit()
        return claimed > 0
    except Exception as err:
        db_session.rollback()
        print(f"Failed to claim paused execution {thread_id}: {err}")
        return False
    finally:
        db_session.close()


def _update_execution_status(
    thread_id: str,
    status: Optional[str] = None,
    current_step: Optional[str] = None,
    error: Optional[str] = None,
    completed: bool = False,
):
    """Helper to update WorkflowExecutionModel status and step in DB."""
    from src.db.session import SessionLocal

    db_session = SessionLocal()
    try:
        rec = (
            db_session.query(WorkflowExecutionModel)
            .filter(WorkflowExecutionModel.thread_id == thread_id)
            .first()
        )
        if rec:
            if status is not None:
                rec.status = status
            if current_step is not None:
                rec.current_step = current_step
            if error is not None:
                rec.error = error
            if completed:
                rec.completed_at = datetime.utcnow()
            rec.updated_at = datetime.utcnow()
            db_session.commit()
    except Exception as err:
        print(f"Failed to update execution {thread_id}: {err}")
    finally:
        db_session.close()


def _terminal_status(values: Dict[str, Any]) -> tuple:
    """Decide whether a graph that has drained to END actually succeeded.

    Returns (status, error). An error always wins over a success status, since
    `status` is a single last-writer-wins slot that an earlier node can leave
    looking healthy after a later one failed.
    """
    values = values or {}
    graph_status = values.get("status")
    node_error = values.get("error")
    if node_error or graph_status in ("failed", "error", "failure"):
        return "failed", node_error or f"A step failed (status '{graph_status}')"
    return "completed", None


#: Run statuses that will never execute another node. A checkpoint left over
#: from an abandoned run still lists the node it was paused at, so these must
#: not be reported as "up next" or the UI shows a gate that can never be acted
#: on.
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "rejected"})


def _summarize_run_state(
    config: WorkflowConfig,
    values: Dict[str, Any],
    next_steps=(),
    run_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Flatten a checkpointer snapshot into per-node outputs/artifacts."""
    values = values or {}
    if run_status in TERMINAL_RUN_STATUSES:
        next_steps = ()
    artifacts = values.get("artifacts") or {}
    nodes: Dict[str, Any] = {}
    for node in config.nodes:
        node_id = node.id
        nodes[node_id] = {
            "output": artifacts.get(f"{node_id}_output"),
            "artifacts": artifacts.get(f"{node_id}_artifacts") or [],
            "exit_code": artifacts.get(f"{node_id}_exit_code"),
            "commit": artifacts.get(f"{node_id}_commit"),
            "commit_url": artifacts.get(f"{node_id}_commit_url"),
            "pr_url": artifacts.get(f"{node_id}_pr_url"),
            "outputs": artifacts.get(f"{node_id}_outputs") or {},
            "workspace": artifacts.get(f"{node_id}_workspace"),
        }
    steps = _summarize_steps(config, artifacts, next_steps, run_status, values)
    return {
        "graph_status": values.get("status"),
        "approval_status": values.get("approval_status"),
        "approval_reviewer": values.get("approval_reviewer"),
        "approval_comment": values.get("approval_comment"),
        "next": list(next_steps or []),
        "inputs": values.get("inputs") or {},
        "pr_url": run_pr_url(artifacts),
        "steps": steps,
        "nodes": nodes,
    }


def run_pr_url(artifacts: Dict[str, Any]) -> Optional[str]:
    """The pull request a run opened, if any node opened one."""
    return next(
        (v for k, v in (artifacts or {}).items() if k.endswith("_pr_url") and v),
        None,
    )


def _sync_incident(thread_id: str, run_status: str, values: Optional[Dict[str, Any]] = None) -> None:
    """Mirror a run's outcome onto the incident that started it (if any).

    A run that drains to END without a PR counts as `failed` for the incident
    even when the graph took its failure branch cleanly: no fix shipped.
    """
    from src.db.session import SessionLocal

    pr_url = run_pr_url((values or {}).get("artifacts") or {})
    if run_status in ("running", "paused", "rejected"):
        status = run_status
    else:
        status = "pr_open" if pr_url else "failed"

    db_session = SessionLocal()
    try:
        incident = (
            db_session.query(IncidentModel)
            .filter(IncidentModel.thread_id == thread_id)
            .first()
        )
        if incident:
            incident.status = status
            if pr_url:
                incident.pr_url = pr_url
            db_session.commit()
    except Exception as err:
        db_session.rollback()
        print(f"Failed to sync incident for {thread_id}: {err}")
    finally:
        db_session.close()


def _summarize_steps(
    config: WorkflowConfig,
    artifacts: Dict[str, Any],
    next_steps=(),
    run_status: Optional[str] = None,
    values: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Derive a per-node progress summary in declared graph order.

    This is the high-level view: it answers "where is the run" without
    exposing raw output. Status is inferred from what the checkpoint actually
    holds rather than from a separate log, so it stays truthful after a reload.

      completed  the node ran and exited 0
      failed     the node ran and exited non-zero
      running    the node is executing right now
      waiting    the run is paused at this node's approval gate
      pending    the node has not started
    """
    next_set = set(next_steps or ())
    values = values or {}
    # The dispatcher records every node that finished, so this is authoritative.
    # The artifact scan is a fallback for checkpoints written before that existed.
    recorded = set(values.get("completed_steps") or ())
    # A node that throws records nothing, but the graph names it in `current_step`.
    failed_step = values.get("current_step") if values.get("error") else None
    steps: List[Dict[str, Any]] = []

    for index, node in enumerate(config.nodes):
        node_id = node.id
        exit_code = artifacts.get(f"{node_id}_exit_code")
        has_run = node_id in recorded or any(
            key.startswith(f"{node_id}_") for key in artifacts
        )

        if exit_code is not None and exit_code != 0:
            status = "failed"
        elif has_run:
            status = "completed"
        elif node_id == failed_step:
            status = "failed"
        elif node_id in next_set:
            # The graph is paused in front of this node, so it has not run yet.
            status = "waiting" if run_status == "paused" else "running"
        else:
            status = "pending"

        steps.append(
            {
                "id": node_id,
                "index": index,
                "status": status,
                "exit_code": exit_code,
                "requires_approval": bool(node.requires_approval),
                "artifact_count": len(artifacts.get(f"{node_id}_artifacts") or []),
            }
        )

    return steps


def run_workflow_sync(
    thread_id: str,
    workflow_id: str,
    checkpointer,
    loop: asyncio.AbstractEventLoop,
    is_resume: bool = False,
    decision: Optional[dict] = None,
    inputs: Optional[Dict[str, Any]] = None,
):
    """Background task that runs the workflow synchronously.

    `inputs` are the run inputs for a fresh run (workflow defaults already
    merged in by the caller); they are fixed in the initial state.

    `decision` carries the human verdict when `is_resume` is set:
    {"approved": bool, "comment": str|None, "reviewer": str|None}. A rejection
    is terminal — the gated node is never executed and the run ends `rejected`.
    """
    from src.db.session import SessionLocal

    db = SessionLocal()
    try:
        config, agents_map, node_types_map = _resolve_workflow(workflow_id, db)
    except Exception as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        _update_execution_status(thread_id, status="failed", error=str(detail), completed=True)
        event_bus.publish_sync(
            thread_id, {"event": "error", "data": detail}, loop=loop
        )
        return
    finally:
        db.close()

    try:
        context = ExecutionContext(
            thread_id=thread_id,
            event_bus=event_bus,
            checkpointer=checkpointer,
        )

        # Reuse the graph the original run compiled so the resume continues
        # against the very checkpoint that paused, not a fresh compilation.
        graph = compile_workflow(
            config, agents_map, node_types_map, context, checkpointer
        )
        config_dict = {"configurable": {"thread_id": thread_id}}

        initial_state = None
        if not is_resume:
            initial_state = {
                "messages": [],
                "current_step": "start",
                "artifacts": {},
                "approval_status": "pending",
                "completed_steps": [],
                "inputs": dict(inputs or {}),
            }
            _update_execution_status(thread_id, status="running", current_step="start")
            event_bus.publish_sync(
                thread_id,
                {"event": "start", "data": f"Initializing workflow '{config.name}'..."},
                loop=loop,
            )
        else:
            approved = True if decision is None else decision.get("approved", True)
            reviewer = (decision or {}).get("reviewer")
            comment = (decision or {}).get("comment")
            approval_status = "approved" if approved else "rejected"

            # Record the verdict in the checkpoint before anything else, so the
            # state a later reader sees explains why the run moved on.
            try:
                graph.update_state(
                    config_dict,
                    {
                        "approval_status": approval_status,
                        "approval_reviewer": reviewer,
                        "approval_comment": comment,
                    },
                )
            except Exception as e:
                print(f"Note on state update: {e}")

            if not approved:
                # The gate is a hard stop: the node it guards never runs, and the
                # graph is abandoned mid-flight. /state reports no pending step
                # for a terminal run, so the checkpoint is left untouched here.
                _update_execution_status(
                    thread_id,
                    status="rejected",
                    error=comment or "Rejected by reviewer",
                    completed=True,
                )
                _sync_incident(thread_id, "rejected")
                event_bus.publish_sync(
                    thread_id,
                    {
                        "event": "rejected",
                        "data": comment
                        or "Run rejected by reviewer. Downstream steps will not run.",
                    },
                    loop=loop,
                )
                return

            _update_execution_status(thread_id, status="running")
            event_bus.publish_sync(
                thread_id,
                {"event": "resume", "data": "Workflow resumed. Executing approved node..."},
                loop=loop,
            )

        stream_input = None if is_resume else initial_state

        for event in graph.stream(stream_input, config_dict):
            step_name = list(event.keys())[0] if event else "unknown"
            if step_name == "__interrupt__":
                continue
            _update_execution_status(thread_id, current_step=step_name)
            event_bus.publish_sync(
                thread_id,
                {"event": "agent", "data": f"Completed step: {step_name}"},
                loop=loop,
            )

        state = graph.get_state(config_dict)
        if state.next:
            next_step = state.next[0] if isinstance(state.next, (list, tuple)) else str(state.next)
            _update_execution_status(thread_id, status="paused", current_step=next_step)
            _sync_incident(thread_id, "paused", state.values)
            event_bus.publish_sync(
                thread_id,
                {"event": "review", "data": f"Waiting for human approval at: {state.next}"},
                loop=loop,
            )
        else:
            # A node that fails still lets the graph drain to END, so reaching
            # the end is not the same as succeeding. Without this check a run
            # whose publish step threw would report `completed`.
            final_status, final_error = _terminal_status(state.values or {})
            _sync_incident(thread_id, final_status, state.values)
            if final_status == "failed":
                _update_execution_status(
                    thread_id, status="failed", error=final_error, completed=True
                )
                event_bus.publish_sync(
                    thread_id, {"event": "error", "data": final_error}, loop=loop
                )
            else:
                _update_execution_status(thread_id, status="completed", completed=True)
                event_bus.publish_sync(
                    thread_id,
                    {"event": "complete", "data": "Workflow finished."},
                    loop=loop,
                )

    except Exception as e:
        _update_execution_status(thread_id, status="failed", error=str(e), completed=True)
        _sync_incident(thread_id, "failed")
        event_bus.publish_sync(
            thread_id, {"event": "error", "data": str(e)}, loop=loop
        )


def start_run(
    workflow: WorkflowModel,
    db: Session,
    request: Request,
    background_tasks: BackgroundTasks,
    inputs: Optional[Dict[str, Any]] = None,
    before_run=None,
) -> str:
    """Create the execution row and queue the run; returns the thread id.

    Run inputs are the workflow's declared defaults overlaid with `inputs`.
    `before_run(inputs) -> inputs` runs in the background worker just before
    the graph starts, for slow preparation (e.g. fetching a stack trace) that
    must not hold up a webhook response.
    """
    thread_id = str(uuid.uuid4())
    db.add(WorkflowExecutionModel(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        workflow_id=workflow.id,
        status="running",
        current_step="start",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    db.commit()

    merged = {**(workflow.inputs or {}), **(inputs or {})}
    checkpointer = request.app.state.checkpointer
    loop = asyncio.get_running_loop()

    def run():
        prepared = before_run(merged) if before_run else merged
        run_workflow_sync(
            thread_id, workflow.id, checkpointer, loop, is_resume=False, inputs=prepared
        )

    background_tasks.add_task(run)
    return thread_id


@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    body: Optional[ExecuteWorkflowRequest] = None,
):
    """Execute a saved workflow, optionally with run inputs."""
    workflow = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    thread_id = start_run(
        workflow, db, request, background_tasks, inputs=(body.inputs if body else None)
    )
    return {"status": "started", "thread_id": thread_id, "workflow_id": workflow_id}


@router.post("/{thread_id}/resume")
async def resume_workflow(
    thread_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Resume a paused workflow (after human approval).

    Kept as a thin alias for POST /{thread_id}/review with decision="approve" so
    existing callers and the original approve-only client keep working.
    """
    return await review_workflow(
        thread_id,
        request,
        background_tasks,
        db,
        ReviewDecisionRequest(decision="approve"),
    )


@router.post("/{thread_id}/review")
async def review_workflow(
    thread_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    review: Optional[ReviewDecisionRequest] = None,
):
    """Record a human verdict on a run paused at an approval gate.

    The gate is derived from the persisted execution row, not from the event
    stream, so a reviewer who reloads the page (or opens the run later) can
    still act on a run that is genuinely waiting.
    """
    execution = (
        db.query(WorkflowExecutionModel)
        .filter(WorkflowExecutionModel.thread_id == thread_id)
        .first()
    )
    if not execution:
        raise HTTPException(
            status_code=404,
            detail=f"Execution for thread '{thread_id}' not found.",
        )

    if execution.status != "paused":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot review execution in status '{execution.status}'. "
                "It is not waiting for approval."
            ),
        )

    review = review or ReviewDecisionRequest(decision="approve")
    approved = review.approved
    decision = {
        "approved": approved,
        "comment": review.comment,
        "reviewer": review.reviewer,
    }

    if not _claim_paused_execution(thread_id):
        # Another reviewer acted first; their verdict is the one that stands.
        raise HTTPException(
            status_code=409,
            detail="This run was already reviewed. Refresh to see the outcome.",
        )

    checkpointer = request.app.state.checkpointer
    loop = asyncio.get_running_loop()

    # The worker owns the terminal status from here, so a rejection and a
    # run that fails mid-resume can't race each other into the execution row.
    background_tasks.add_task(
        run_workflow_sync,
        thread_id,
        execution.workflow_id,
        checkpointer,
        loop,
        is_resume=True,
        decision=decision,
    )

    return {
        "status": "resumed" if approved else "rejected",
        "decision": "approved" if approved else "rejected",
        "thread_id": thread_id,
        "workflow_id": execution.workflow_id,
    }


@router.get("/{thread_id}/status")
async def get_workflow_status(
    thread_id: str,
    db: Session = Depends(get_db),
):
    """Get the current execution status of a workflow run."""
    execution = (
        db.query(WorkflowExecutionModel)
        .filter(WorkflowExecutionModel.thread_id == thread_id)
        .first()
    )
    if not execution:
        raise HTTPException(
            status_code=404,
            detail=f"Execution for thread '{thread_id}' not found.",
        )

    return {
        "thread_id": execution.thread_id,
        "workflow_id": execution.workflow_id,
        "status": execution.status,
        "current_step": execution.current_step,
        "error": execution.error,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
    }


@router.get("/{thread_id}/state")
async def get_workflow_state(
    thread_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Persisted per-node outputs/artifacts for a run, read from the checkpointer."""
    execution = (
        db.query(WorkflowExecutionModel)
        .filter(WorkflowExecutionModel.thread_id == thread_id)
        .first()
    )
    if not execution:
        raise HTTPException(
            status_code=404,
            detail=f"Execution for thread '{thread_id}' not found.",
        )

    config, agents_map, node_types_map = _resolve_workflow(execution.workflow_id, db)
    checkpointer = request.app.state.checkpointer
    context = ExecutionContext(
        thread_id=thread_id,
        event_bus=event_bus,
        checkpointer=checkpointer,
    )
    graph = compile_workflow(config, agents_map, node_types_map, context, checkpointer)

    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        values = snapshot.values or {}
        next_steps = snapshot.next or ()
    except Exception:
        # Unknown/expired thread, or an in-memory checkpointer after a restart.
        values, next_steps = {}, ()

    return {
        "thread_id": execution.thread_id,
        "workflow_id": execution.workflow_id,
        "status": execution.status,
        "current_step": execution.current_step,
        "error": execution.error,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        **_summarize_run_state(
            config, values, next_steps, run_status=execution.status
        ),
    }


@router.get("/{workflow_id}/executions")
def list_workflow_executions(
    workflow_id: str,
    db: Session = Depends(get_db),
):
    """List all execution runs for a specific workflow."""
    executions = (
        db.query(WorkflowExecutionModel)
        .filter(WorkflowExecutionModel.workflow_id == workflow_id)
        .order_by(WorkflowExecutionModel.created_at.desc())
        .all()
    )
    return [
        {
            "thread_id": ex.thread_id,
            "workflow_id": ex.workflow_id,
            "status": ex.status,
            "current_step": ex.current_step,
            "error": ex.error,
            "created_at": ex.created_at.isoformat() if ex.created_at else None,
            "updated_at": ex.updated_at.isoformat() if ex.updated_at else None,
            "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
        }
        for ex in executions
    ]

