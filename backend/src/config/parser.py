import yaml
from pydantic import ValidationError
from typing import Dict, Any, List

from .schema import WorkflowConfig, WorkflowSpec, RuntimeConfig


def _read_yaml(file_path: str) -> dict:
    """Read a YAML file into a dict, raising a clean ValueError on failure."""
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML file: {e}")
    except Exception as e:
        raise ValueError(f"Error loading configuration: {e}")

    if not data:
        raise ValueError("Configuration file is empty.")

    return data


def _validation_error(e: ValidationError) -> ValueError:
    error_msgs = []
    for error in e.errors():
        loc = ".".join([str(l) for l in error.get('loc', [])])
        msg = error.get('msg', 'Unknown error')
        error_msgs.append(f"- {loc}: {msg}")
    return ValueError(f"Configuration validation failed:\n" + "\n".join(error_msgs))


def load_config(file_path: str) -> WorkflowConfig:
    """
    Loads and validates a workflow configuration from a YAML file.
    
    Args:
        file_path (str): The path to the YAML configuration file.
        
    Returns:
        WorkflowConfig: The validated configuration object.
        
    Raises:
        ValueError: If there is a YAML parsing error or Pydantic validation error.
    """
    data = _read_yaml(file_path)
    try:
        return WorkflowConfig(**data)
    except ValidationError as e:
        raise _validation_error(e)


def parse_workflow_spec(data: dict) -> WorkflowSpec:
    """Validate a raw dict (from YAML) into a WorkflowSpec."""
    try:
        return WorkflowSpec(**data)
    except ValidationError as e:
        raise _validation_error(e)


def load_workflow_spec(file_path: str) -> WorkflowSpec:
    """
    Loads and validates a self-contained workflow YAML file
    (runtimes + agents + graph) into a WorkflowSpec.
    """
    return parse_workflow_spec(_read_yaml(file_path))


def load_runtimes(file_path: str) -> List[RuntimeConfig]:
    """
    Loads a list of RuntimeConfig from a YAML file.

    Supports either a bare list of runtimes or a dict with a top-level
    ``runtimes:`` key (so a workflow spec file can be reused).
    """
    data = _read_yaml(file_path)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "runtimes" in data:
        items = data["runtimes"]
    else:
        raise ValueError("Runtime file must be a list or contain a 'runtimes' key.")

    try:
        return [RuntimeConfig(**item) for item in items]
    except ValidationError as e:
        raise _validation_error(e)
