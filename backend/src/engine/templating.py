"""
Param templating — ``{{ dotted.path }}`` placeholders resolved from run inputs.

A workflow YAML is written once and reused across repos and incidents, so the
values that change per run (the target repo, the alert title, the test
command) are placeholders that the orchestrator fills in just before a node
runs:

    env:
      TEST_CMD: "{{ test_command }}"
    instructions: "Fix: {{ incident.title }}"

Rules:
  - A string that is exactly one placeholder keeps the value's type, so
    ``repos: "{{ repos }}"`` can splice in a list.
  - Placeholders embedded in a longer string are stringified.
  - A missing path renders as an empty string; it never raises, because a
    half-filled prompt is easier to debug than a node that refuses to start.

This is deliberately not Jinja: no expressions, no filters, nothing that can
execute. ``${VAR}`` (host env interpolation in the cli_agent) is untouched.
"""
import json
import re
from typing import Any, Dict

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def lookup(context: Dict[str, Any], path: str) -> Any:
    """Resolve ``a.b.0.c`` against nested dicts/lists; None when absent."""
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
        if current is None:
            return None
    return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def render(value: Any, context: Dict[str, Any]) -> Any:
    """Recursively render placeholders in strings inside dicts/lists."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole and value.strip() == value:
            resolved = lookup(context, whole.group(1))
            return "" if resolved is None else resolved
        return _PLACEHOLDER.sub(lambda m: _stringify(lookup(context, m.group(1))), value)
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


def template_context(state: Dict[str, Any], thread_id: str = "") -> Dict[str, Any]:
    """The names a placeholder can reference for a given run state.

    Run inputs are exposed both at the top level (``{{ incident.title }}``) and
    under ``inputs`` (``{{ inputs.incident.title }}``), plus structured node
    outputs as ``{{ outputs.<node>.<key> }}``.
    """
    inputs = dict(state.get("inputs") or {})
    return {
        **inputs,
        "inputs": inputs,
        "outputs": node_outputs(state.get("artifacts") or {}),
        "run": {"thread_id": thread_id},
    }


def node_outputs(artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Collect ``<node>_outputs`` artifact entries into ``{node: outputs}``."""
    return {
        key[: -len("_outputs")]: value
        for key, value in artifacts.items()
        if key.endswith("_outputs") and isinstance(value, dict)
    }


def first_output(outputs: Dict[str, Any], key: str, default: Any = None) -> Any:
    """The value of `key` from whichever node's outputs define it (graph order).

    Lets consumers ask "was it reproduced?" without hardcoding node ids, so
    nodes can be renamed in the YAML freely.
    """
    for values in outputs.values():
        if isinstance(values, dict) and key in values:
            return values[key]
    return default
