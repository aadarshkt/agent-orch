# Executor Onboarding Guide

This guide documents the complete flow for adding a new executor type, creating
instances of it (via UI or YAML), and the migration/seeding decision. It uses a
`pipeline_trigger` executor (trigger a CI/Jenkins job) as the running example,
but the procedure is identical for any new type.

> Note on state: the self-describing executor registry and YAML import are now
> implemented (branch `feat/yaml-driven-cli-onboarding`). Node types are derived
> from the executor registry (no hand-maintained seed), and workflows can be
> imported from self-contained YAML. The DB remains a projection for the UI.

## 1. Mental model

Four distinct concepts, each with a single responsibility:

| Concept | What it is | Lives in | Created by |
|---|---|---|---|
| Executor (type) | Python class that runs one kind of work | code (`@register_executor`) | developer |
| Runtime | Named environment preset (image, binary, endpoint, skills, packages) | YAML `runtimes:` | data |
| Agent (instance) | A concrete node = type + runtime + inputs | YAML `agents:` or UI | data |
| Workflow | Graph of agent references (nodes + edges) | YAML `workflow:` or UI | data |

The rule that makes everything repeatable: **a type is code, a node is data.**
You write one class to define a *kind* of work; you write YAML (or use the UI)
to create any number of *instances* of that kind.

## 2. What "new type" actually means

A new type is a new execution strategy. Examples:

- `cli_agent` - run a local CLI binary (subprocess or container)
- `cloud_agent` - call a remote agent API
- `pipeline_trigger` - trigger and optionally poll a CI job
- `script_runner` - run a single Python script/shell command

Each type differs in two ways:

1. **Execution logic** - what `execute()` actually does.
2. **Input params** - what configuration it accepts (`input_schema`), and what
   environment it needs (`runtime_kind`).

Everything else (registry, UI rendering, execution dispatch, state passing) is
generic and already handled by the platform.

## 3. Anatomy of an executor

```python
# backend/src/executors/pipeline_trigger_executor.py
import asyncio
from typing import Any, Dict
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("pipeline_trigger", runtime_kind="cloud")
class PipelineTriggerExecutor(BaseExecutor):
    input_schema = {
        "type": "object",
        "properties": {
            "job_name": {
                "type": "string",
                "title": "Job Name",
                "description": "CI job to trigger",
            },
            "params": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "title": "Build Params",
            },
            "wait_for_completion": {
                "type": "boolean",
                "default": False,
                "title": "Wait for completion",
            },
            "poll_interval": {
                "type": "number",
                "default": 30,
                "title": "Poll interval (seconds)",
            },
        },
        "required": ["job_name"],
    }

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        ...
```

The three parts that matter:

- **`@register_executor("key", runtime_kind="...")`** - the registry key and
  what kind of environment it consumes.
- **`input_schema`** - JSON Schema describing the params. This is what drives
  the UI form automatically.
- **`execute()`** - the actual work. Receives the resolved node config, current
  state, and runtime context; returns updated state.

## 4. Input params: types and config

`input_schema` is standard JSON Schema. The frontend maps each field to a
widget with zero frontend code. This is the complete mapping:

| JSON Schema | UI widget | Example |
|---|---|---|
| `type: "string"` | text input | `job_name` |
| `type: "string"` + `ui:widget: "textarea"` | textarea | `prompt` |
| `type: "string"` + `enum: [...]` | dropdown (select) | `model: [gemini-pro, claude-sonnet]` |
| `type: "number"` / `"integer"` | number input | `timeout`, `poll_interval` |
| `type: "boolean"` | toggle | `wait_for_completion` |
| `type: "array"` + `items: {type: "string"}` | multi-input list | `gitlab_urls` |
| `type: "object"` + `additionalProperties` | key-value editor | `params`, `env`, `headers` |

Adding a field = adding one entry to `input_schema`. Every agent of that type
gets the new field immediately. `default` pre-fills values; `required` marks
fields; `title`/`description` become the form label/help text.

## 5. `runtime_kind` and runtime resolution

`runtime_kind` declares what environment the executor needs. Values:

- `cli` - runs a local binary/container. Resolves to `image`, `command`,
  `skills`, `packages`, `env`.
- `cloud` - calls a remote API. Resolves to `endpoint`, `model`, `env`.
- `mcp` - connects to an MCP server. Resolves to `server` connection details.
- `none` - self-contained (like `script_runner`). No runtime required.

At execution time the executor does:

```python
runtime = resolve_runtime(node_config["runtime"])  # look up runtimes:<name>
```

`resolve_runtime` merges the agent's `inputs.env` over the runtime's base `env`,
so per-run secrets/overrides layer on top of the preset without baking them
into the image.

## 6. Complete onboarding procedure

For a new type, follow these steps in order:

1. **Create the executor file** in `backend/src/executors/` with the
   `@register_executor` decorator, `runtime_kind`, `input_schema`, and
   `execute()`.

2. **Register it on import** by adding it to `backend/src/executors/__init__.py`:

   ```python
   from src.executors import (
       pipeline_trigger_executor,   # <-- new
       ...
   )
   ```

   `import src.executors` (done at startup) triggers the decorator and puts the
   class in the in-memory registry. No other registration is needed.

3. **Add a runtime preset** (only if `runtime_kind` is not `none` and the
   environment is new) to the YAML `runtimes:` section.

4. **Create instances** via UI or YAML (see section 8).

5. **Restart the backend** so the new module is imported.

That is the entire onboarding. There is no seed table edit, no migration, no
frontend change.

### Example `execute()` for pipeline_trigger

```python
async def execute(self, node_config, state, context):
    node_id = node_config["id"]
    agent_name = node_config.get("agent_name", node_id)
    inputs = node_config["params"]
    runtime = resolve_runtime(node_config["runtime"])   # {endpoint, env}

    context.event_bus.publish_sync(
        context.thread_id,
        {"event": "agent", "data": f"[{agent_name}] Triggering {inputs['job_name']}..."},
    )

    # 1. Trigger the job
    build_url = await trigger_job(runtime["endpoint"], inputs["job_name"], inputs.get("params"))

    # 2. Optionally poll until done
    status = "triggered"
    if inputs.get("wait_for_completion"):
        status = await poll_build(build_url, inputs.get("poll_interval", 30))

    # 3. Record artifacts for downstream nodes
    artifacts = dict(state.get("artifacts", {}))
    artifacts[f"{node_id}_build_url"] = build_url
    artifacts[f"{node_id}_build_status"] = status

    messages = list(state.get("messages", []))
    messages.append({"role": "assistant", "content": f"Job {inputs['job_name']} {status}: {build_url}"})

    return {
        **state,
        "messages": messages,
        "artifacts": artifacts,
        "current_step": node_id,
        "status": "completed" if status == "success" else "failed",
        "error": None if status == "success" else f"Build {status}",
    }
```

## 7. How the type becomes known and runs

```
startup                -> import src.executors -> registry populated in memory
GET /executors         -> returns key + runtime_kind + input_schema (from class)
UI (DynamicForm)       -> renders the form from input_schema
workflow execute       -> resolve agent -> type -> executor_key -> get_executor(key)
executor.execute(...)  -> resolves runtime, runs logic, returns updated state
LangGraph              -> routes to next node using edges + condition
```

The dispatch path is always: **data (agent) references a type (executor_key),
the registry instantiates the class, Python executes.**

## 8. Creating instances: UI vs YAML

Both produce identical data; they are two views of the same thing.

**UI path**

1. Agents page -> pick the `pipeline_trigger` type card -> the form renders
   from `input_schema` -> fill `job_name`, `params`, toggles -> save
   (`POST /agents`).
2. Workflow builder -> drag the agent into the canvas -> set approval/edges ->
   save (`POST /workflows`).

**YAML path**

Add an `agents:` entry and a `workflow.nodes:` entry.

## 9. Full self-contained YAML example

```yaml
version: 1

runtimes:
  jenkins:
    kind: cloud
    endpoint: https://jenkins.eco.dev
    env: { JENKINS_TOKEN: "${JENKINS_TOKEN}" }

agents:
  build:
    type: pipeline_trigger
    runtime: jenkins
    inputs:
      job_name: ecocharge-build
      params: { branch: "feature/x" }
      wait_for_completion: true
      poll_interval: 30

workflow:
  name: "EcoCharge Build"
  hitl_enabled: true
  nodes:
    - { id: build, agent: build, requires_approval: true }
  edges:
    - { from: build, to: deploy, condition: success }
```

## 10. Migration vs seeding

The decision depends on whether you keep the DB as a projection:

- **Type metadata (`node_types`):** in the target design, no migration and no
  hand-maintained seed. The executor class is the source of truth, and
  `GET /executors` derives metadata directly from the in-memory registry. If a
  DB `node_types` table is kept for querying, populate it **on startup by
  syncing from the registry** (derive, not hand-edit). The current
  `seed_node_types.py` + `BUILT_IN_NODE_TYPES` list becomes redundant and can
  be deleted.

- **Instances (`agents`) and `workflows`:** pure data. Adding one is a YAML/UI
  change, not a migration. When YAML becomes the source of truth, parsing the
  YAML and upserting into the DB is the "migration" (a projection sync), not a
  hand-written schema migration.

- **Adding a field to an existing executor's `input_schema`:** a schema change.
  Existing agent instances are unaffected because `params` is JSON and the
  executor's `default` value fills gaps. No formal DB migration is required;
  only the executor class changes.

Summary: **seed types from the executor registry (automatic), treat
agents/workflows as data (no migration), and backfill new fields via schema
`default`s (no migration).**

## 11. Dependency / artifact handoff

Ordering is guaranteed by edges and sequential execution. Data is shared via
`WorkflowState` (`messages` + `artifacts`). To make downstream nodes depend on
a committed artifact explicitly:

1. The producing executor writes a reference into `artifacts`
   (`artifacts["commit"]`, `artifacts["build_url"]`).
2. The consuming executor reads that reference instead of re-cloning blindly.

This pins the dependency instead of relying on "the previous node pushed
somewhere." Optionally add a `depends_on` field to the node schema to make the
relationship visible in YAML.

## 12. Implementation status

| Area | Before | Now |
|---|---|---|
| Type metadata | hand-maintained `seed_node_types.py` + DB `node_types` | derived from executor registry |
| Registration | `@register_executor` + manual seed entry | `@register_executor` only |
| Workflow source | DB tables | YAML import (`POST /workflows/import`) + DB projection |
| Runtime concept | none | `runtimes:` presets + `runtime_kind` + `GET /runtimes` |
| UI form | rendered DB `config_schema` | renders executor `input_schema` + runtime dropdown |

## 13. Packaging a CLI agent image

One image per agent. The image holds the static runtime (binary, language
runtime, packages, bundled skills); dynamic inputs (repo, branch, prompt, env)
are injected at execution.

1. Author a `Dockerfile` from `backend/docker/agent.Dockerfile`, baking in the
   CLI binary + packages.
2. Build and push: `backend/docker/build.sh <registry>/<image> [context]`.
   The executor pulls it via `docker run` using credentials from `backend/.env`
   (`REGISTRY_USERNAME`, `REGISTRY_PASSWORD`, `REGISTRY_URL`).
3. Register it as a runtime in `backend/config/runtimes.yaml`:

   ```yaml
   - name: my-agent
     kind: cli
     image: ghcr.io/org/my-agent:latest
     command: ["python", "-m", "my_agent"]
   ```

4. Reference it from an agent in a workflow YAML via `runtime: my-agent`.

## 14. HITL after successful completion

HITL for a CLI run is expressed through graph ordering, not a new mechanism: a
CLI agent node runs to completion, then a `reviewer` node with
`requires_approval: true` (or any approval node) follows it. The orchestrator's
`interrupt_before` pauses at the review node after the CLI node succeeds. Edge
conditions (e.g. `condition: success`) ensure the review only runs when the CLI
node completed cleanly.
