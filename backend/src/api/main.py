from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from src.db.session import init_db
from src.engine.checkpointer import init_checkpointer, close_checkpointer
from src.api.routes import workflows, mcp, events

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables and checkpointer
    try:
        init_db()
    except Exception as e:
        print(f"Database initialization failed: {e}")
    
    init_checkpointer(app)
    
    yield
    
    # Shutdown: clean up connection pools
    close_checkpointer(app)

app = FastAPI(title="Agent Orchestrator API", version="1.0.0", lifespan=lifespan)

# Allow CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router, prefix="/workflows", tags=["Workflows"])
app.include_router(mcp.router, prefix="/mcp-registry", tags=["MCP Registry"])
app.include_router(events.router, prefix="/events", tags=["Events"])

