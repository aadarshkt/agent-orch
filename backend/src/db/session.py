import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from src.db.models import Base

# Load .env file from backend root
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# SQLAlchemy requires 'postgresql+psycopg://' driver dialect
if DATABASE_URL.startswith("postgresql://"):
    SQLALCHEMY_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    SQLALCHEMY_DATABASE_URL = DATABASE_URL

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Columns added after a table first shipped. create_all() never alters an
# existing table, so these are added in place on startup.
_ADDED_COLUMNS = {
    "workflows": {"inputs": "JSON", "triggers": "JSON"},
}


def _add_missing_columns(bind) -> None:
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def init_db():
    Base.metadata.create_all(bind=engine)
    _add_missing_columns(engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

