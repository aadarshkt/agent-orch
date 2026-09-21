"""
Seed Node Types (registry-derived).

Node types are no longer hand-maintained here. They are derived from the
executor registry: each @register_executor class declares its own
``input_schema``, ``runtime_kind``, ``display_name``, and ``icon``. This seeder
syncs that metadata into the node_types table (upsert) on startup, so adding a
new executor auto-registers its node type with zero seed edits.
"""
import uuid
from datetime import datetime
from src.db.session import SessionLocal
from src.db.models import NodeTypeModel
from src.registry.executor_registry import list_executors

# Ensure executors are registered before deriving node types
import src.executors  # noqa: F401


def _build_node_types() -> list:
    """Map executor registry metadata into node-type rows."""
    node_types = []
    for ex in list_executors():
        node_types.append(
            {
                "type_key": ex["key"],
                "display_name": ex["display_name"],
                "description": ex["description"],
                "executor_key": ex["key"],
                "config_schema": ex["input_schema"],
                "default_config": ex["default_config"],
                "icon": ex["icon"],
            }
        )
    return node_types


def seed_node_types():
    """
    Idempotent seeder: upserts node types derived from the executor registry.
    Call during application startup.
    """
    db = SessionLocal()
    try:
        for node_type_data in _build_node_types():
            existing = (
                db.query(NodeTypeModel)
                .filter(NodeTypeModel.type_key == node_type_data["type_key"])
                .first()
            )
            if existing:
                # Sync metadata so config_schema changes propagate
                existing.display_name = node_type_data["display_name"]
                existing.description = node_type_data["description"]
                existing.executor_key = node_type_data["executor_key"]
                existing.config_schema = node_type_data["config_schema"]
                existing.default_config = node_type_data["default_config"]
                existing.icon = node_type_data["icon"]
                existing.updated_at = datetime.utcnow()
                print(f"  Synced node type: {node_type_data['type_key']}")
            else:
                node_type = NodeTypeModel(
                    id=str(uuid.uuid4()),
                    type_key=node_type_data["type_key"],
                    display_name=node_type_data["display_name"],
                    description=node_type_data.get("description", ""),
                    executor_key=node_type_data["executor_key"],
                    config_schema=node_type_data["config_schema"],
                    default_config=node_type_data.get("default_config"),
                    icon=node_type_data.get("icon"),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(node_type)
                print(f"  Seeded node type: {node_type_data['type_key']}")

        db.commit()
        print("Node type seeding complete.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding node types: {e}")
    finally:
        db.close()
