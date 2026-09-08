from typing import TypedDict, List, Dict, Any, Optional

class WorkflowState(TypedDict):
    messages: List[Dict[str, Any]]
    current_step: Optional[str]
    artifacts: Dict[str, Any]
    approval_status: Optional[str]
    error: Optional[str]
    status: Optional[str]
