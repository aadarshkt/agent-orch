"""
Cloud Agent Executor.

Calls a cloud-hosted agent API with artifacts pulled from git repositories.
Handles the full lifecycle: pull artifacts → call agent API → return results.
"""
import asyncio
from typing import Any, Dict
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("cloud_agent")
class CloudAgentExecutor(BaseExecutor):
    """Calls a cloud-hosted agent API with artifacts pulled from git repos."""

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        node_id = node_config["id"]
        agent_name = node_config.get("agent_name", node_id)
        params = node_config.get("params", {})

        gitlab_urls = params.get("gitlab_urls", [])
        system_prompt = params.get("system_prompt", "")
        model = params.get("model", "gemini-pro")

        # --- Step 1: Pull artifacts from git repos ---
        if context.event_bus:
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Pulling artifacts from {len(gitlab_urls)} repo(s)...",
                },
            )

        # TODO: Implement actual git clone / GitLab API fetch
        # For now, simulate the pull
        await asyncio.sleep(1)
        pulled_artifacts = [f"artifact_from_{url.split('/')[-1]}" for url in gitlab_urls]

        # --- Step 2: Call cloud agent API ---
        if context.event_bus:
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Calling cloud agent (model: {model})...",
                },
            )

        # TODO: Implement actual cloud agent API call (Vertex AI, etc.)
        await asyncio.sleep(2)
        agent_response = (
            f"Cloud agent '{agent_name}' processed {len(pulled_artifacts)} artifacts "
            f"with model '{model}'."
        )

        # --- Step 3: Update state ---
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": agent_response})

        return {
            **state,
            "messages": messages,
            "current_step": node_id,
            "status": "completed",
            "error": None,
        }
