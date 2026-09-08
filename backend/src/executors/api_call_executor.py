"""
API Call Executor.

Makes arbitrary HTTP requests with configurable method, URL, headers,
and body. Supports template variables resolved from workflow state.
"""
import asyncio
from typing import Any, Dict
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("api_call")
class APICallExecutor(BaseExecutor):
    """Makes an arbitrary HTTP request with configurable method, URL, headers, body."""

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        node_id = node_config["id"]
        agent_name = node_config.get("agent_name", node_id)
        params = node_config.get("params", {})

        method = params.get("method", "GET")
        url = params.get("url", "")
        headers = params.get("headers", {})
        body_template = params.get("body_template", "")

        # --- Step 1: Resolve template variables from state ---
        # TODO: Implement Jinja2/simple template resolution
        # e.g., {{state.artifacts.report}} → actual value
        resolved_body = body_template

        if context.event_bus:
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Making {method} request to {url}...",
                },
            )

        # --- Step 2: Make HTTP request ---
        # TODO: Implement actual HTTP request using httpx or aiohttp
        await asyncio.sleep(1)
        response_data = {
            "status_code": 200,
            "body": f"Simulated {method} response from {url}",
        }

        if context.event_bus:
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] API call completed (status: {response_data['status_code']}).",
                },
            )

        # --- Step 3: Update state ---
        messages = list(state.get("messages", []))
        messages.append(
            {
                "role": "system",
                "content": f"API Call ({method} {url}) → {response_data['status_code']}",
            }
        )

        artifacts = dict(state.get("artifacts", {}))
        artifacts[f"{node_id}_response"] = response_data

        return {
            **state,
            "messages": messages,
            "artifacts": artifacts,
            "current_step": node_id,
            "status": "completed",
            "error": None,
        }
