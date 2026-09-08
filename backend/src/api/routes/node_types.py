"""
Node Types API — Read-only endpoints for fetching registered node types.

Node types are seeded on startup and managed by developers, not by users.
The UI fetches these to know what agent types are available and what
config fields each type requires (via config_schema).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from src.db.session import get_db
from src.db.models import NodeTypeModel
from src.registry.executor_registry import list_executors

router = APIRouter()


@router.get("/")
def get_node_types(db: Session = Depends(get_db)):
    """List all registered node types with their config schemas."""
    node_types = db.query(NodeTypeModel).all()
    return [
        {
            "id": nt.id,
            "type_key": nt.type_key,
            "display_name": nt.display_name,
            "description": nt.description,
            "executor_key": nt.executor_key,
            "config_schema": nt.config_schema,
            "default_config": nt.default_config,
            "icon": nt.icon,
            "created_at": nt.created_at.isoformat() if nt.created_at else None,
        }
        for nt in node_types
    ]


@router.get("/executors")
def get_executors():
    """List available executor backends (from in-memory registry)."""
    return list_executors()


@router.get("/{type_key}")
def get_node_type(type_key: str, db: Session = Depends(get_db)):
    """Get a single node type's full details by type_key."""
    nt = db.query(NodeTypeModel).filter(NodeTypeModel.type_key == type_key).first()
    if not nt:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Node type '{type_key}' not found.")
    return {
        "id": nt.id,
        "type_key": nt.type_key,
        "display_name": nt.display_name,
        "description": nt.description,
        "executor_key": nt.executor_key,
        "config_schema": nt.config_schema,
        "default_config": nt.default_config,
        "icon": nt.icon,
        "created_at": nt.created_at.isoformat() if nt.created_at else None,
    }
