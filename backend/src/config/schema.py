from typing import List, Literal, Optional, Union, Dict, Any, Annotated
from pydantic import BaseModel, Field, ConfigDict

class BaseNodeConfig(BaseModel):
    id: str
    type: str
    requires_approval: bool = False

class AgentNodeConfig(BaseNodeConfig):
    type: Literal["agent"]
    agent_id: str
    system_prompt: Optional[str] = None
    tools: Optional[List[str]] = None

class PipelineNodeConfig(BaseNodeConfig):
    type: Literal["pipeline"]
    pipeline_id: str
    steps: List[str]

class ToolNodeConfig(BaseNodeConfig):
    type: Literal["tool"]
    tool_name: str
    parameters: Optional[Dict[str, Any]] = None

class MCPNodeConfig(BaseNodeConfig):
    type: Literal["mcp"]
    server: str
    action: str
    parameters: Optional[Dict[str, Any]] = None

NodeConfigType = Annotated[
    Union[
        AgentNodeConfig, 
        PipelineNodeConfig, 
        ToolNodeConfig, 
        MCPNodeConfig
    ],
    Field(discriminator="type")
]

class EdgeConfig(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    condition: Optional[str] = None
    
    model_config = ConfigDict(populate_by_name=True)

class WorkflowConfig(BaseModel):
    name: str
    description: Optional[str] = None
    hitl_enabled: bool = False
    nodes: List[NodeConfigType]
    edges: List[EdgeConfig]
