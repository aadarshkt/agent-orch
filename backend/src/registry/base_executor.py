from abc import ABC, abstractmethod
from typing import Any, Dict
from dataclasses import dataclass, field


@dataclass
class ExecutionContext:
    """
    Carries runtime dependencies into executors.
    
    Passed by the orchestrator at execution time so that executors
    can publish SSE events, access the checkpointer, etc. without
    needing to import global singletons.
    """
    thread_id: str
    event_bus: Any = None          # src.engine.pubsub.EventBus
    checkpointer: Any = None       # LangGraph checkpointer
    extra: Dict[str, Any] = field(default_factory=dict)  # Future: credential_store, etc.


class BaseExecutor(ABC):
    """
    Abstract base class for all node execution strategies.
    
    Each executor handles a specific type of work (calling a cloud agent,
    making an API call, running a script, etc.). Executors are stateless —
    all configuration comes from node_config.
    
    Subclasses must implement the `execute` method. Each executor fully
    owns its execution lifecycle: if it needs delays, retries, custom
    SSE events, or special error handling, it does so internally.
    """

    @abstractmethod
    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """
        Execute the node's logic and return updated workflow state.

        Args:
            node_config: The full node configuration dict containing:
                - id: unique node id in the workflow
                - type: the node_type key (e.g. "cloud_agent")
                - agent_name: human-readable name of the agent instance
                - params: dict of type-specific config
            state: Current WorkflowState dict (messages, artifacts, etc.)
            context: Runtime dependencies (event bus, thread_id, etc.)

        Returns:
            Updated WorkflowState dict.
        """
        ...
