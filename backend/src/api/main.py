from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from src.db.session import init_db
from src.engine.checkpointer import init_checkpointer, close_checkpointer
from src.api.routes import workflows, mcp, events, node_types, agents
from src.registry.seed_node_types import seed_node_types

# Ensure executors are registered on import
import src.executors  # noqa: F401

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables, seed data, and checkpointer
    try:
        init_db()
        print("Database tables initialized.")
    except Exception as e:
        print(f"Database initialization failed: {e}")
    
    # Seed built-in node types (idempotent)
    try:
        seed_node_types()
    except Exception as e:
        print(f"Node type seeding failed: {e}")
    
    init_checkpointer(app)
    
    yield
    
    # Shutdown: clean up connection pools
    close_checkpointer(app)

app = FastAPI(title="Agent Orchestrator API", version="2.0.0", lifespan=lifespan)

# Allow CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(node_types.router, prefix="/node-types", tags=["Node Types"])
app.include_router(agents.router, prefix="/agents", tags=["Agents"])
app.include_router(workflows.router, prefix="/workflows", tags=["Workflows"])
app.include_router(mcp.router, prefix="/mcp-registry", tags=["MCP Registry"])
app.include_router(events.router, prefix="/events", tags=["Events"])
