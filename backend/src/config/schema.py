from typing import List, Optional, Dict, Any, Literal
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

class Source(BaseModel):
    """
    The single way to point at an asset.

    A git repo checked out at a ref, optionally narrowed to a path within it.
    Used for repos, skills, context and prompt — one shape everywhere.

    - repo: git URL
    - ref:  branch | tag | sha (always explicit)
    - path: directory or file within the repo; "" means the whole repo
    """
    repo: str
    ref: str
    path: str = ""


class RuntimeConfig(BaseModel):
    """A named runtime preset (environment) referenced by agents."""
    name: str
    kind: str = "cli"                       # cli | cloud | mcp | none
    image: Optional[str] = None
    command: Optional[List[str]] = None
    endpoint: Optional[str] = None
    model: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    required_env: List[str] = Field(default_factory=list)  # env vars that must be present
    resource_limits: Optional[Dict[str, Any]] = None  # {"cpus": "1", "memory": "512m"}
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


class TriggerSpec(BaseModel):
    """An alert source allowed to start this workflow over a webhook.

    POST /triggers/{workflow_id}/{source}. The incident is normalized, deduped
    by fingerprint within ``dedupe_window_hours``, and the run starts with
    ``inputs = {**workflow.inputs, **trigger.inputs, "incident": incident}``.
    """
    source: Literal["sentry", "datadog", "github", "generic"]
    secret_env: Optional[str] = None        # host env var holding the webhook secret
    match: Dict[str, Any] = Field(default_factory=dict)   # incident field -> required value
    inputs: Dict[str, Any] = Field(default_factory=dict)  # per-trigger input overrides
    dedupe_window_hours: float = 24.0


class WorkflowGraphSpec(BaseModel):
    name: str
    description: Optional[str] = None
    hitl_enabled: bool = False
    # Default run inputs; params reference them as {{ name }}.
    inputs: Dict[str, Any] = Field(default_factory=dict)
    triggers: List[TriggerSpec] = Field(default_factory=list)
    nodes: List[WorkflowNodeSpec]
    edges: List[EdgeConfig]


class WorkflowSpec(BaseModel):
    """A self-contained workflow file: runtimes + agents + graph."""
    version: int = 1
    runtimes: List[RuntimeConfig] = Field(default_factory=list)
    agents: Dict[str, AgentDef] = Field(default_factory=dict)
    workflow: WorkflowGraphSpec
