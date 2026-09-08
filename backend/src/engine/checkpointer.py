import psycopg
from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver
from contextlib import contextmanager

@contextmanager
def get_checkpointer(connection_string: str):
    """
    Returns a configured PostgresSaver that can be used with LangGraph.
    It will create the necessary tables if they do not exist.
    """
    # Use standard configurations for LangGraph postgres saver
    # autocommit=True is typically recommended/required for PostgresSaver pool
    kwargs = {
        "autocommit": True,
        "prepare_threshold": 0,
    }
    
    with ConnectionPool(conninfo=connection_string, max_size=20, kwargs=kwargs) as pool:
        checkpointer = PostgresSaver(pool)
        
        # NOTE: calling setup() creates the required tables if they don't exist
        checkpointer.setup()
        
        yield checkpointer

def setup_checkpointer(connection_string: str):
    """
    Helper function to explicitly run the setup.
    """
    with get_checkpointer(connection_string) as checkpointer:
        # Setup is already called in the context manager
        pass
