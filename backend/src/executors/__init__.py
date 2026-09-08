"""
Auto-imports all executor modules to trigger @register_executor decorators.
Add new executor imports here when creating new execution strategies.
"""
from src.executors import (
    cloud_agent_executor,
    mcp_agent_executor,
    reviewer_executor,
    api_call_executor,
    script_runner_executor,
    sleep_executor,
)
