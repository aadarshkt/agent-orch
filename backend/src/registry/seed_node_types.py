"""
Seed Node Types.

Idempotent seeder that populates the node_types table with built-in types
on first startup. Checks if each type_key already exists before inserting.
"""
import uuid
from datetime import datetime
from src.db.session import SessionLocal
from src.db.models import NodeTypeModel


BUILT_IN_NODE_TYPES = [
    {
        "type_key": "cloud_agent",
        "display_name": "Cloud Agent",
        "description": "Calls a cloud-hosted agent API with artifacts pulled from git repositories.",
        "executor_key": "cloud_agent",
        "icon": "cloud",
        "config_schema": {
            "type": "object",
            "properties": {
                "system_prompt": {
                    "type": "string",
                    "title": "System Prompt",
                    "description": "Instructions for the cloud agent",
                    "ui:widget": "textarea",
                },
                "gitlab_urls": {
                    "type": "array",
                    "items": {"type": "string", "format": "uri"},
                    "title": "GitLab Repository URLs",
                    "description": "Repository URLs to pull artifacts from",
                },
                "model": {
                    "type": "string",
                    "enum": ["gemini-pro", "gemini-flash", "claude-sonnet"],
                    "default": "gemini-pro",
                    "title": "Model",
                    "description": "LLM model to use",
                },
            },
            "required": ["system_prompt", "gitlab_urls"],
        },
        "default_config": {"model": "gemini-pro"},
    },
    {
        "type_key": "mcp_agent",
        "display_name": "MCP Agent",
        "description": "Cloud agent that uses MCP (Model Context Protocol) tool servers like Figma or GitHub.",
        "executor_key": "mcp_agent",
        "icon": "plug",
        "config_schema": {
            "type": "object",
            "properties": {
                "system_prompt": {
                    "type": "string",
                    "title": "System Prompt",
                    "description": "Instructions for the MCP agent",
                    "ui:widget": "textarea",
                },
                "mcp_server_name": {
                    "type": "string",
                    "title": "MCP Server",
                    "description": "Name of the MCP server to connect to (e.g., figma, github)",
                },
                "action": {
                    "type": "string",
                    "title": "Action",
                    "description": "The action to perform on the MCP server",
                },
            },
            "required": ["system_prompt", "mcp_server_name"],
        },
        "default_config": {},
    },
    {
        "type_key": "reviewer",
        "display_name": "Reviewer",
        "description": "Generates a review report and optionally posts to GitLab or returns to user.",
        "executor_key": "reviewer",
        "icon": "clipboard-check",
        "config_schema": {
            "type": "object",
            "properties": {
                "review_prompt": {
                    "type": "string",
                    "title": "Review Prompt",
                    "description": "Instructions for the review (what to look for, criteria, etc.)",
                    "ui:widget": "textarea",
                },
                "output_target": {
                    "type": "string",
                    "enum": ["user", "gitlab_mr_comment", "gitlab_issue"],
                    "default": "user",
                    "title": "Output Target",
                    "description": "Where to send the review report",
                },
                "gitlab_project": {
                    "type": "string",
                    "title": "GitLab Project",
                    "description": "GitLab project path (required if output target is GitLab)",
                },
            },
            "required": ["review_prompt"],
        },
        "default_config": {"output_target": "user"},
    },
    {
        "type_key": "api_call",
        "display_name": "API Call",
        "description": "Makes an arbitrary HTTP request with configurable method, URL, headers, and body.",
        "executor_key": "api_call",
        "icon": "globe",
        "config_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                    "default": "GET",
                    "title": "HTTP Method",
                },
                "url": {
                    "type": "string",
                    "format": "uri",
                    "title": "URL",
                    "description": "The endpoint URL to call",
                },
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "title": "Headers",
                    "description": "HTTP headers as key-value pairs",
                },
                "body_template": {
                    "type": "string",
                    "title": "Body Template",
                    "description": "Request body (supports {{state.variable}} placeholders)",
                    "ui:widget": "textarea",
                },
            },
            "required": ["url"],
        },
        "default_config": {"method": "GET", "headers": {}},
    },
    {
        "type_key": "script_runner",
        "display_name": "Script Runner",
        "description": "Runs a Python script or shell command as a subprocess with configurable timeout.",
        "executor_key": "script_runner",
        "icon": "terminal",
        "config_schema": {
            "type": "object",
            "properties": {
                "script_path": {
                    "type": "string",
                    "title": "Script Path",
                    "description": "Path to the Python script file to execute",
                },
                "inline_script": {
                    "type": "string",
                    "title": "Inline Script",
                    "description": "Python code to execute directly (alternative to script_path)",
                    "ui:widget": "textarea",
                },
                "timeout": {
                    "type": "number",
                    "default": 60,
                    "title": "Timeout (seconds)",
                    "description": "Maximum execution time before the script is killed",
                },
            },
            "required": [],
        },
        "default_config": {"timeout": 60},
    },
    {
        "type_key": "sleep",
        "display_name": "Sleep / Delay",
        "description": "Simple delay node for testing and demonstration purposes.",
        "executor_key": "sleep",
        "icon": "clock",
        "config_schema": {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "default": 1,
                    "title": "Duration (seconds)",
                    "description": "How long to sleep",
                },
            },
            "required": ["duration"],
        },
        "default_config": {"duration": 1},
    },
]


def seed_node_types():
    """
    Idempotent seeder: inserts built-in node types if they don't already exist.
    Call during application startup.
    """
    db = SessionLocal()
    try:
        for node_type_data in BUILT_IN_NODE_TYPES:
            existing = (
                db.query(NodeTypeModel)
                .filter(NodeTypeModel.type_key == node_type_data["type_key"])
                .first()
            )
            if not existing:
                node_type = NodeTypeModel(
                    id=str(uuid.uuid4()),
                    type_key=node_type_data["type_key"],
                    display_name=node_type_data["display_name"],
                    description=node_type_data.get("description", ""),
                    executor_key=node_type_data["executor_key"],
                    config_schema=node_type_data["config_schema"],
                    default_config=node_type_data.get("default_config"),
                    icon=node_type_data.get("icon"),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(node_type)
                print(f"  Seeded node type: {node_type_data['type_key']}")
            else:
                print(f"  Node type already exists: {node_type_data['type_key']}")

        db.commit()
        print("Node type seeding complete.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding node types: {e}")
    finally:
        db.close()
