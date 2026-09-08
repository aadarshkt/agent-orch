"""
Sleep Executor.

Simple delay executor for testing and demonstration purposes.
"""
import asyncio
from typing import Any, Dict
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("sleep")
class SleepExecutor(BaseExecutor):
    """Simple delay executor (for testing/demo purposes)."""

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        node_id = node_config["id"]
        agent_name = node_config.get("agent_name", node_id)
        params = node_config.get("params", {})

        duration = params.get("duration", 1)

        if context.event_bus:
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Sleeping for {duration}s...",
                },
            )

        await asyncio.sleep(duration)

        if context.event_bus:
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Sleep completed.",
                },
            )

        messages = list(state.get("messages", []))
        messages.append(
            {"role": "system", "content": f"Slept for {duration}s ({agent_name})"}
        )

        return {
            **state,
            "messages": messages,
            "current_step": node_id,
            "status": "completed",
            "error": None,
        }
