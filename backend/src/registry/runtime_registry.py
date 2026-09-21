"""
Runtime registry — in-memory store of named runtime presets.

Runtimes are the "preconfigured environment" layer: Docker image, command,
endpoint, env, resource limits. They are data (loaded from YAML), not code.

Registry pull credentials are read from environment variables (a single
``backend/.env`` file, already loaded at import time by ``src.db.session``):

- REGISTRY_USERNAME
- REGISTRY_PASSWORD
- REGISTRY_URL (optional, defaults to Docker Hub)
"""
import os
from typing import Dict, List

from src.config.schema import RuntimeConfig

_RUNTIMES: Dict[str, RuntimeConfig] = {}


def register_runtime(runtime: RuntimeConfig) -> None:
    _RUNTIMES[runtime.name] = runtime


def register_runtimes(runtimes: List[RuntimeConfig]) -> None:
    for rt in runtimes:
        register_runtime(rt)


def resolve_runtime(name: str) -> RuntimeConfig:
    if name not in _RUNTIMES:
        available = list(_RUNTIMES.keys())
        raise ValueError(f"Unknown runtime '{name}'. Available runtimes: {available}")
    return _RUNTIMES[name]


def list_runtimes() -> List[dict]:
    return [
        {
            "name": rt.name,
            "kind": rt.kind,
            "image": rt.image,
            "command": rt.command,
            "endpoint": rt.endpoint,
            "model": rt.model,
            "timeout": rt.timeout,
        }
        for rt in _RUNTIMES.values()
    ]


def get_registry_creds() -> Dict[str, str]:
    """Return registry pull credentials from environment variables."""
    return {
        "username": os.getenv("REGISTRY_USERNAME", ""),
        "password": os.getenv("REGISTRY_PASSWORD", ""),
        "registry": os.getenv("REGISTRY_URL", ""),
    }
