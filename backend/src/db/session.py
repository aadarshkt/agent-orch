from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base

CONNECTION_STRING = "postgresql+psycopg://postgres:postgres@localhost:5433/postgres"

engine = create_engine(CONNECTION_STRING)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
