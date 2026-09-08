from fastapi import APIRouter

router = APIRouter()

@router.get("/")
def get_capabilities():
    return {
        "agents": ["ReasoningAgent", "DataAgent"],
        "pipelines": ["DataExtractionPipeline"],
        "mcp_tools": [] # This would be fetched dynamically from registered MCPs
    }
