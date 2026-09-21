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


def _derive_default_config(input_schema: dict) -> dict:
    """Collect per-field ``default`` values from an input_schema."""
    if not input_schema or "properties" not in input_schema:
        return {}
    return {
        key: prop["default"]
        for key, prop in input_schema["properties"].items()
        if isinstance(prop, dict) and "default" in prop
    }


def list_executors() -> List[dict]:
    """Return metadata about all registered executors (for UI dropdowns / API)."""
    result = []
    for key, cls in _EXECUTOR_REGISTRY.items():
        input_schema = getattr(cls, "input_schema", None)
        result.append(
            {
                "key": key,
                "name": cls.__name__,
                "description": (cls.__doc__ or "").strip(),
                "display_name": getattr(cls, "display_name", key),
                "icon": getattr(cls, "icon", None),
                "runtime_kind": getattr(cls, "runtime_kind", None),
                "input_schema": input_schema,
                "default_config": getattr(cls, "default_config", None)
                or _derive_default_config(input_schema),
            }
        )
    return result
