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
from src.db.models import WorkflowModel, AgentModel, NodeTypeModel, WorkflowExecutionModel
from src.config.schema import WorkflowConfig, NodeConfig, EdgeConfig, WorkflowSpec
from src.config.parser import load_workflow_spec, parse_workflow_spec
from src.engine.orchestrator import compile_workflow
from src.engine.pubsub import event_bus
from src.registry.base_executor import ExecutionContext
from src.registry.runtime_registry import register_runtimes, resolve_runtime
from src.registry.executor_registry import list_executors

# Ensure executors are registered on import
import src.executors  # noqa: F401

router = APIRouter()


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

    # 2. Validate agent type + runtime references
    valid_executors = {e["key"] for e in list_executors()}
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

    # 3. Validate node -> agent references
    node_ids = {n.id for n in spec.workflow.nodes}
    for node in spec.workflow.nodes:
        if node.agent not in spec.agents:
            raise HTTPException(
                status_code=422,
                detail=f"Node '{node.id}' references unknown agent '{node.agent}'.",
            )

    # 4. Validate git_commit source_node_id references a real node
    for node in spec.workflow.nodes:
        agent_def = spec.agents[node.agent]
        if agent_def.type == "git_commit":
            src = agent_def.inputs.get("source_node_id")
            if src and src not in node_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"Agent '{node.agent}': source_node_id '{src}' "
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
        {"from": e.from_node, "to": e.to_node, "condition": e.condition}
        for e in spec.workflow.edges
    ]

    # 7. Upsert workflow
    workflow = (
        db.query(WorkflowModel)
        .filter(WorkflowModel.name == spec.workflow.name)
        .first()
    )
    if workflow:
        workflow.description = spec.workflow.description
        workflow.hitl_enabled = spec.workflow.hitl_enabled
        workflow.nodes = nodes
        workflow.edges = edges
        workflow.updated_at = datetime.utcnow()
    else:
        workflow = WorkflowModel(
            id=str(uuid.uuid4()),
            name=spec.workflow.name,
            description=spec.workflow.description,
            hitl_enabled=spec.workflow.hitl_enabled,
            nodes=nodes,
            edges=edges,
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
            EdgeConfig(**{"from": e["from_node"], "to": e["to_node"], "condition": e.get("condition")})
            for e in workflow.edges
        ],
    )

    return config, agents_map, node_types_map


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


def run_workflow_sync(
    thread_id: str,
    workflow_id: str,
    checkpointer,
    loop: asyncio.AbstractEventLoop,
    is_resume: bool = False,
):
    """Background task that runs the workflow synchronously."""
    from src.db.session import SessionLocal

    db = SessionLocal()
    try:
        config, agents_map, node_types_map = _resolve_workflow(workflow_id, db)
    except HTTPException as e:
        _update_execution_status(thread_id, status="failed", error=str(e.detail), completed=True)
        event_bus.publish_sync(
            thread_id, {"event": "error", "data": e.detail}, loop=loop
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
            }
            _update_execution_status(thread_id, status="running", current_step="start")
            event_bus.publish_sync(
                thread_id,
                {"event": "start", "data": f"Initializing workflow '{config.name}'..."},
                loop=loop,
            )
        else:
            _update_execution_status(thread_id, status="running")
            # Update state in checkpointer to approve paused node
            try:
                graph.update_state(config_dict, {"approval_status": "approved"})
            except Exception as e:
                print(f"Note on state update: {e}")
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
            event_bus.publish_sync(
                thread_id,
                {"event": "review", "data": f"Waiting for human approval at: {state.next}"},
                loop=loop,
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
        event_bus.publish_sync(
            thread_id, {"event": "error", "data": str(e)}, loop=loop
        )


@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Execute a saved workflow."""
    # Verify workflow exists
    workflow = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    thread_id = str(uuid.uuid4())
    execution = WorkflowExecutionModel(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        workflow_id=workflow_id,
        status="running",
        current_step="start",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(execution)
    db.commit()

    checkpointer = request.app.state.checkpointer
    loop = asyncio.get_running_loop()

    background_tasks.add_task(
        run_workflow_sync, thread_id, workflow_id, checkpointer, loop, is_resume=False
    )

    return {"status": "started", "thread_id": thread_id, "workflow_id": workflow_id}


@router.post("/{thread_id}/resume")
async def resume_workflow(
    thread_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Resume a paused workflow (after human approval)."""
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

    if execution.status not in ("paused", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume execution in status '{execution.status}'. Must be 'paused'.",
        )

    execution.status = "running"
    execution.updated_at = datetime.utcnow()
    db.commit()

    checkpointer = request.app.state.checkpointer
    loop = asyncio.get_running_loop()

    event_bus.publish_sync(
        thread_id,
        {"event": "resume", "data": "Workflow resumed by user. Executing next step..."},
        loop=loop,
    )

    background_tasks.add_task(
        run_workflow_sync,
        thread_id,
        execution.workflow_id,
        checkpointer,
        loop,
        is_resume=True,
    )

    return {
        "status": "resumed",
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

