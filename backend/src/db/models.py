from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()

class WorkflowConfigModel(Base):
    __tablename__ = "workflow_configs"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    yaml_content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MCPServerRegistration(Base):
    __tablename__ = "mcp_servers"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    server_type = Column(String, nullable=False) # 'stdio' or 'sse'
    command = Column(String, nullable=True) # for stdio
    args = Column(JSON, nullable=True) # for stdio
    url = Column(String, nullable=True) # for sse
    created_at = Column(DateTime, default=datetime.utcnow)
