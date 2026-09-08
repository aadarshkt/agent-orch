from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.memory import MemorySaver
from contextlib import contextmanager
from src.db.session import DATABASE_URL

def init_checkpointer(app):
    """
    Initializes the LangGraph checkpointer for the FastAPI application state.
    Attempts to connect to PostgreSQL with a connection pool; if unavailable,
    gracefully falls back to MemorySaver.
    """
    pool = None
    try:
        pool = ConnectionPool(
            conninfo=DATABASE_URL,
            max_size=20,
            timeout=3.0,
            kwargs={"autocommit": True, "prepare_threshold": 0}
        )
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        app.state.checkpointer = checkpointer
        app.state.pool = pool
        print("Initialized PostgresSaver checkpointer successfully.")
    except Exception as e:
        if pool:
            try:
                pool.close()
            except Exception:
                pass
        print(f"Postgres checkpointer connection failed, falling back to MemorySaver: {e}")
        app.state.checkpointer = MemorySaver()
        app.state.pool = None


def close_checkpointer(app):
    """
    Closes the checkpointer connection pool during application shutdown.
    """
    pool = getattr(app.state, "pool", None)
    if pool:
        try:
            pool.close()
            print("Closed Postgres checkpointer connection pool.")
        except Exception as e:
            print(f"Error closing Postgres pool: {e}")

@contextmanager
def get_checkpointer(connection_string: str = None):
    """
    Returns a configured PostgresSaver context manager for standalone or CLI execution.
    """
    conn = connection_string or DATABASE_URL
    kwargs = {
        "autocommit": True,
        "prepare_threshold": 0,
    }
    with ConnectionPool(conninfo=conn, max_size=20, kwargs=kwargs) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        yield checkpointer

