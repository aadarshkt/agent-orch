from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class NodeConfig(BaseModel):
    """
    Universal node config used in workflows.
    
    Each node references an agent_id (a registered Agent instance).
    The agent's type-specific params are resolved from the DB at execution time,
    not stored inline in the workflow config.
    """
    id: str                                  # Unique node ID within the workflow
    agent_id: str                            # References a registered Agent's ID
    requires_approval: bool = False


class EdgeConfig(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    condition: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class WorkflowConfig(BaseModel):
    name: str
    description: Optional[str] = None
    hitl_enabled: bool = False
    nodes: List[NodeConfig]
    edges: List[EdgeConfig]


# ──────────────────────────────────────────────────────────────
# Self-contained YAML spec: runtimes + agents + graph in one file.
# These are the YAML-facing models (source of truth), distinct from the
# DB-facing models above.
# ──────────────────────────────────────────────────────────────

class RuntimeConfig(BaseModel):
    """A named runtime preset (environment) referenced by agents."""
    name: str
    kind: str = "cli"                       # cli | cloud | mcp | none
    image: Optional[str] = None
    command: Optional[List[str]] = None
    endpoint: Optional[str] = None
    model: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    resource_limits: Optional[Dict[str, Any]] = None  # {"cpus": "1", "memory": "512m"}
    packages: Optional[Dict[str, Any]] = None          # {"install": "..."}
    timeout: int = 300


class AgentDef(BaseModel):
    """A named agent instance (keyed by name in the agents map)."""
    type: str                               # executor_key (e.g. "cli_agent")
    runtime: Optional[str] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)


class WorkflowNodeSpec(BaseModel):
    """A graph node referencing an agent by name within the same file."""
    id: str
    agent: str                              # key in WorkflowSpec.agents
    requires_approval: bool = False


class WorkflowGraphSpec(BaseModel):
    name: str
    description: Optional[str] = None
    hitl_enabled: bool = False
    nodes: List[WorkflowNodeSpec]
    edges: List[EdgeConfig]


class WorkflowSpec(BaseModel):
    """A self-contained workflow file: runtimes + agents + graph."""
    version: int = 1
    runtimes: List[RuntimeConfig] = Field(default_factory=list)
    agents: Dict[str, AgentDef] = Field(default_factory=dict)
    workflow: WorkflowGraphSpec
