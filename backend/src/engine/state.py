from typing import TypedDict, List, Dict, Any, Optional

class WorkflowState(TypedDict):
    messages: List[Dict[str, Any]]
    # Per-run inputs (the incident that triggered the run, the target repo, the
    # test command). Set once when the run starts and never rewritten, so every
    # node sees the same values; params reference them as {{ dotted.path }}.
    inputs: Dict[str, Any]
    current_step: Optional[str]
    artifacts: Dict[str, Any]
    approval_status: Optional[str]
    # Nodes that have finished, in the order they finished. Executors disagree
    # about what they record — a cloud agent stamps no artifacts at all — so
    # progress cannot be inferred from artifacts. The dispatcher records it here
    # instead, which is the single point every node passes through.
    completed_steps: List[str]
    # Who made the last review call and why. Persisted in the checkpoint so a
    # paused run can be reopened and still show the decision that released it.
    approval_reviewer: Optional[str]
    approval_comment: Optional[str]
    error: Optional[str]
    status: Optional[str]
