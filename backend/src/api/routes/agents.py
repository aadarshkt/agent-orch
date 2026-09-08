"""
Agents API — Full CRUD for user-created agent instances.

Users create agents by selecting a node type and filling in the type-specific
params. The params are validated against the node type's config_schema
(JSON Schema validation) before saving.
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from src.db.session import get_db
from src.db.models import AgentModel, NodeTypeModel

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

router = APIRouter()


class CreateAgentRequest(BaseModel):
    name: str
    description: Optional[str] = None
    node_type_key: str
    params: Dict[str, Any]


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


def _validate_params(params: dict, config_schema: dict):
    """Validate params against the node type's JSON Schema."""
    if not HAS_JSONSCHEMA:
        print("Warning: jsonschema not installed, skipping param validation.")
        return
    try:
        jsonschema.validate(instance=params, schema=config_schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Params validation failed: {e.message} (at path: {list(e.absolute_path)})",
        )


@router.post("/")
def create_agent(req: CreateAgentRequest, db: Session = Depends(get_db)):
    """Create a new agent from an existing node type."""
    # Verify node type exists
    node_type = (
        db.query(NodeTypeModel)
        .filter(NodeTypeModel.type_key == req.node_type_key)
        .first()
    )
    if not node_type:
        raise HTTPException(
            status_code=404,
            detail=f"Node type '{req.node_type_key}' not found.",
        )

    # Validate params against schema
    _validate_params(req.params, node_type.config_schema)

    # Check for duplicate name
    existing = db.query(AgentModel).filter(AgentModel.name == req.name).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Agent with name '{req.name}' already exists.",
        )

    agent = AgentModel(
        id=str(uuid.uuid4()),
        name=req.name,
        description=req.description,
        node_type_key=req.node_type_key,
        params=req.params,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "node_type_key": agent.node_type_key,
        "params": agent.params,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


@router.get("/")
def list_agents(db: Session = Depends(get_db)):
    """List all registered agents."""
    agents = db.query(AgentModel).all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "node_type_key": a.node_type_key,
            "params": a.params,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in agents
    ]


@router.get("/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    """Get agent details by ID."""
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "node_type_key": agent.node_type_key,
        "params": agent.params,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


@router.put("/{agent_id}")
def update_agent(
    agent_id: str, req: UpdateAgentRequest, db: Session = Depends(get_db)
):
    """Update an agent's config."""
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

    if req.params is not None:
        # Re-validate against schema
        node_type = (
            db.query(NodeTypeModel)
            .filter(NodeTypeModel.type_key == agent.node_type_key)
            .first()
        )
        if node_type:
            _validate_params(req.params, node_type.config_schema)
        agent.params = req.params

    if req.name is not None:
        # Check for duplicate name
        existing = (
            db.query(AgentModel)
            .filter(AgentModel.name == req.name, AgentModel.id != agent_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Agent with name '{req.name}' already exists.",
            )
        agent.name = req.name

    if req.description is not None:
        agent.description = req.description

    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)

    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "node_type_key": agent.node_type_key,
        "params": agent.params,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


@router.delete("/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    """Delete an agent."""
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

    db.delete(agent)
    db.commit()
    return {"status": "deleted", "id": agent_id}
