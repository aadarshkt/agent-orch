import yaml
from pydantic import ValidationError
from typing import Dict, Any

from .schema import WorkflowConfig

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
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            
        if not data:
            raise ValueError("Configuration file is empty.")
            
        return WorkflowConfig(**data)
        
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML file: {e}")
    except ValidationError as e:
        # Provide a cleaner error message
        errors = e.errors()
        error_msgs = []
        for error in errors:
            loc = ".".join([str(l) for l in error.get('loc', [])])
            msg = error.get('msg', 'Unknown error')
            error_msgs.append(f"- {loc}: {msg}")
            
        raise ValueError(f"Configuration validation failed:\n" + "\n".join(error_msgs))
    except Exception as e:
        raise ValueError(f"Error loading configuration: {e}")
