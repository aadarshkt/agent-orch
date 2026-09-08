from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from src.db.session import init_db
from src.api.routes import workflows, mcp, events
from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver

# We can import CONNECTION_STRING from checkpointer or define it here.
# Assuming standard connection string used in backend/main.py
CONNECTION_STRING = "postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        init_db()
    except Exception as e:
        print(f"Database initialization failed: {e}")
    
    # Initialize checkpointer pool
    try:
        pool = ConnectionPool(
            conninfo=CONNECTION_STRING,
            max_size=20,
            kwargs={"autocommit": True, "prepare_threshold": 0}
        )
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        app.state.checkpointer = checkpointer
        app.state.pool = pool
    except Exception as e:
        print(f"Postgres checkpointer failed, falling back to MemorySaver: {e}")
        from langgraph.checkpoint.memory import MemorySaver
        app.state.checkpointer = MemorySaver()
        app.state.pool = None
    
    yield
    
    # Shutdown
    if getattr(app.state, "pool", None):
        app.state.pool.close()

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

