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
