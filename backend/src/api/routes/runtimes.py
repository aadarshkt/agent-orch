"""
Runtimes API — read-only listing of preconfigured runtime presets.

The UI uses this to populate the "Runtime" dropdown for CLI/cloud agents.
"""
from fastapi import APIRouter
from src.registry.runtime_registry import list_runtimes

router = APIRouter()


@router.get("/")
def get_runtimes():
    return list_runtimes()
