from sqlalchemy import Column, String, Text, DateTime, JSON, Boolean
from sqlalchemy.orm import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()


class NodeTypeModel(Base):
    """
    Tier 2: Node type blueprint.
    Defines what config fields an agent of this type needs.
    Seeded on startup, rarely changed.
    """
    __tablename__ = "node_types"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type_key = Column(String, nullable=False, unique=True)       # "cloud_agent", "mcp_agent", etc.
    display_name = Column(String, nullable=False)                 # "Cloud Agent"
    description = Column(Text, nullable=True)                     # Human-readable
    executor_key = Column(String, nullable=False)                 # Maps to in-memory executor registry
    config_schema = Column(JSON, nullable=False)                  # JSON Schema for params
    default_config = Column(JSON, nullable=True)                  # Default values
    icon = Column(String, nullable=True)                          # Icon identifier for UI
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentModel(Base):
    """
    Tier 3: User-created agent instance.
    A concrete agent with specific config, based on a node type.
    """
    __tablename__ = "agents"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)            # "Design Reviewer", "Code Writer"
    description = Column(Text, nullable=True)
    node_type_key = Column(String, nullable=False)                # FK reference to node_types.type_key
    params = Column(JSON, nullable=False)                         # Filled-in config (validated against node_type's config_schema)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowModel(Base):
    """
    User-created workflow definition.
    Composed of agents arranged in a graph.
    """
    __tablename__ = "workflows"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    hitl_enabled = Column(Boolean, default=False)
    nodes = Column(JSON, nullable=False)                          # [{id, agent_id, requires_approval}, ...]
    edges = Column(JSON, nullable=False)                          # [{from, to, condition?}, ...]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MCPServerRegistration(Base):
    __tablename__ = "mcp_servers"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    server_type = Column(String, nullable=False)  # 'stdio' or 'sse'
    command = Column(String, nullable=True)        # for stdio
    args = Column(JSON, nullable=True)             # for stdio
    url = Column(String, nullable=True)            # for sse
    created_at = Column(DateTime, default=datetime.utcnow)


class WorkflowExecutionModel(Base):
    """
    Tracks individual execution runs of workflows.
    Maps thread_id to workflow_id and maintains execution status and step history.
    """
    __tablename__ = "workflow_executions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, unique=True, index=True, nullable=False)
    workflow_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="running")  # running, paused, completed, failed
    current_step = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

