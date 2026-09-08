"""
In-memory executor registry.

Executors self-register via the @register_executor decorator when their
module is imported.  This registry is NOT backed by the database — it
lives purely in Python process memory.  The DB's NodeTypeModel.executor_key
field stores a string that references a key in this registry.

Usage:
    from src.registry.executor_registry import register_executor, get_executor

    @register_executor("cloud_agent")
    class CloudAgentExecutor(BaseExecutor):
        async def execute(self, ...): ...

    # Later, at runtime:
    executor = get_executor("cloud_agent")
    result = await executor.execute(node_config, state, context)
"""

from typing import Dict, Type, List
from src.registry.base_executor import BaseExecutor

_EXECUTOR_REGISTRY: Dict[str, Type[BaseExecutor]] = {}


def register_executor(key: str):
    """
    Class decorator that registers an executor under the given key.

    Example:
        @register_executor("cloud_agent")
        class CloudAgentExecutor(BaseExecutor): ...
    """
    def wrapper(cls: Type[BaseExecutor]):
        if key in _EXECUTOR_REGISTRY:
            raise ValueError(
                f"Duplicate executor key '{key}': "
                f"{cls.__name__} vs {_EXECUTOR_REGISTRY[key].__name__}"
            )
        _EXECUTOR_REGISTRY[key] = cls
        return cls
    return wrapper


def get_executor(key: str) -> BaseExecutor:
    """Instantiate and return an executor by its registry key."""
    cls = _EXECUTOR_REGISTRY.get(key)
    if not cls:
        available = list(_EXECUTOR_REGISTRY.keys())
        raise ValueError(
            f"Unknown executor: '{key}'. Available executors: {available}"
        )
    return cls()


def list_executors() -> List[dict]:
    """Return metadata about all registered executors (for UI dropdowns / API)."""
    return [
        {
            "key": key,
            "name": cls.__name__,
            "description": (cls.__doc__ or "").strip(),
        }
        for key, cls in _EXECUTOR_REGISTRY.items()
    ]
