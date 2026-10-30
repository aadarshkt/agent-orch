# CLI agent run: sequence diagrams

The path a workflow takes when its nodes use the `cli_agent` executor, from the
dashboard's Execute button to the Docker container and back. Renders on GitHub,
or paste a block into https://mermaid.live.

## 1. Startup (once per backend process)

```mermaid
sequenceDiagram
    autonumber
    participant Main as FastAPI lifespan<br/>api/main.py
    participant Exec as src.executors
    participant XReg as Executor registry<br/>(in memory)
    participant DB as Postgres (app tables)
    participant RReg as Runtime registry<br/>(in memory)
    participant CP as Checkpointer

    Main->>Exec: import src.executors
    Exec->>XReg: @register_executor("cli_agent") -> CliAgentExecutor
    Main->>DB: init_db() creates tables
    Main->>DB: seed_node_types() (cli_agent node type -> executor_key "cli_agent")
    Main->>RReg: register_runtimes(load_runtimes("config/runtimes.yaml"))
    Main->>CP: init_checkpointer(app)
    alt Postgres reachable
        CP-->>Main: app.state.checkpointer = PostgresSaver(pool)
    else Postgres down
        CP-->>Main: app.state.checkpointer = MemorySaver()
    end
```

## 2. Executing a workflow

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Dashboard<br/>(Next.js)
    participant API as FastAPI<br/>routes/workflows.py
    participant SSE as routes/events.py
    participant Bus as EventBus<br/>(engine/pubsub.py)
    participant DB as Postgres (app tables)
    participant W as Worker thread<br/>run_workflow_sync
    participant Orch as Orchestrator<br/>compile_workflow
    participant LG as LangGraph<br/>compiled StateGraph
    participant CP as Checkpointer
    participant Node as node_func<br/>(dispatcher)
    participant CLI as CliAgentExecutor
    participant Git as git_fetcher
    participant Docker as Docker container<br/>(runtime image)

    User->>UI: click Execute
    UI->>API: POST /workflows/{id}/execute
    API->>DB: insert WorkflowExecution(thread_id, status=running)
    API->>W: BackgroundTasks.add_task(run_workflow_sync, is_resume=False)
    API-->>UI: {thread_id}

    UI->>SSE: GET /events/stream?thread_id=...
    SSE->>Bus: subscribe(thread_id) -> asyncio.Queue
    Note over SSE,Bus: No buffering: events published before<br/>this subscribe are dropped.

    W->>DB: _resolve_workflow: load workflow, agents, node types
    DB-->>W: WorkflowConfig, agents_map, node_types_map

    W->>Orch: compile_workflow(config, agents_map, node_types_map, context, checkpointer)
    loop each node in config.nodes
        Orch->>Orch: graph.add_node(node.id, make_node_func(...))
        opt hitl_enabled and node.requires_approval
            Orch->>Orch: interrupt_before.append(node.id)
        end
    end
    Orch->>Orch: add edges (plain, or conditional via evaluate_condition router)
    Orch->>Orch: START -> nodes with no incoming edge, no-outgoing nodes -> END
    Orch->>LG: graph.compile(checkpointer, interrupt_before)
    LG-->>W: compiled graph

    W->>DB: status=running, current_step=start
    W->>Bus: publish_sync {event: start}
    Bus-->>SSE: queue.put
    SSE-->>UI: data: {"event":"start"}

    W->>LG: graph.stream(initial_state, {thread_id})

    loop for each node LangGraph schedules
        LG->>CP: save checkpoint (state before node)
        LG->>Node: node_func(state)
        Node->>Node: get_executor("cli_agent")
        Node->>CLI: new event loop: execute(exec_config, state, context)

        CLI->>CLI: resolve_runtime(params.runtime) -> image, command, env, timeout
        CLI->>CLI: mkdir $WORKSPACE_ROOT/agent-orch/{thread_id}/{node_id}
        CLI->>Bus: publish {event: agent, "Workspace: ..."}

        loop repos, skills, context, prompt (each a Source {repo, ref, path})
            CLI->>Git: resolve_source(source, dest, cache_root)
            Git->>Git: clone or reuse cached checkout at ref, copy path to dest
        end
        CLI->>CLI: copy upstream {node}_artifacts files into .agent/context/upstream/{node}/
        CLI->>CLI: build env (AGENT_* paths, required_env from host, overrides)
        CLI->>CLI: write .agent/env (secrets go via --env-file, not -e)
        opt REGISTRY_USERNAME / PASSWORD set
            CLI->>Docker: docker login --password-stdin
        end

        CLI->>Docker: docker run --rm --env-file ... -v node_dir:/workspace image cmd
        loop each stdout/stderr line
            Docker-->>CLI: line
            CLI->>Bus: publish {event: agent, line}
            Bus-->>SSE: queue.put
            SSE-->>UI: data: {"event":"agent", ...}
        end

        alt exits before timeout
            Docker-->>CLI: exit code
            CLI->>CLI: glob params.artifacts in node_dir
            CLI-->>Node: state + artifacts[{node}_workspace, _repos, _exit_code, _output, _artifacts],<br/>message, status=completed|failed
        else timeout
            CLI->>Docker: kill
            CLI-->>Node: state with status=failed, error="Timed out after Ns"
        end

        Node->>Node: append node_id to completed_steps
        Note over Node: Any exception becomes<br/>{status: failed, error} instead of raising
        Node-->>LG: new state
        LG->>CP: save checkpoint (state after node)
        LG->>LG: pick next node: fixed edge, or router evaluating<br/>"success" / "failure" / "approved" / expression
        LG-->>W: yield {node_id: state}
        W->>DB: current_step=node_id
        W->>Bus: publish_sync {event: agent, "Completed step: node_id"}
    end

    W->>LG: graph.get_state({thread_id})
    LG->>CP: load latest checkpoint
    alt state.next is set (stopped at an interrupt_before node)
        W->>DB: status=paused, current_step=next
        W->>Bus: publish_sync {event: review}
        SSE-->>UI: review event: show Approve / Reject
    else drained to END with an error in state
        W->>DB: status=failed, error
        W->>Bus: publish_sync {event: error}
    else drained to END cleanly
        W->>DB: status=completed
        W->>Bus: publish_sync {event: complete}
    end
```

## 3. Human review (HITL) and resume

```mermaid
sequenceDiagram
    autonumber
    actor Reviewer
    participant UI as Dashboard
    participant API as FastAPI<br/>routes/workflows.py
    participant DB as Postgres (app tables)
    participant W as Worker thread<br/>run_workflow_sync
    participant LG as LangGraph graph
    participant CP as Checkpointer
    participant Bus as EventBus

    Reviewer->>UI: Approve or Reject (+ comment)
    UI->>API: POST /workflows/{thread_id}/review {decision, comment, reviewer}
    API->>DB: execution.status == paused?
    alt not paused
        API-->>UI: 400
    end
    API->>DB: UPDATE status=running WHERE status=paused (atomic claim)
    alt someone else claimed it first
        API-->>UI: 409 already reviewed
    end
    API->>W: add_task(run_workflow_sync, is_resume=True, decision)
    API-->>UI: {status: resumed | rejected}

    W->>LG: compile_workflow(...) again with the same checkpointer
    W->>LG: update_state({thread_id}, approval_status, reviewer, comment)
    LG->>CP: write checkpoint with the verdict

    alt rejected
        W->>DB: status=rejected, completed
        W->>Bus: publish_sync {event: rejected}
        Note over W,LG: Gated node never runs. Graph is abandoned.
    else approved
        W->>DB: status=running
        W->>Bus: publish_sync {event: resume}
        W->>LG: graph.stream(None, {thread_id})
        LG->>CP: load paused checkpoint
        Note over LG: Continues at the interrupted node.<br/>Same per-node loop as diagram 2.
        W->>LG: get_state -> paused again / failed / completed
    end
```

## Where things live

| Concern | File |
| --- | --- |
| HTTP routes, worker, status bookkeeping | `backend/src/api/routes/workflows.py` |
| SSE endpoint | `backend/src/api/routes/events.py` |
| In-process pub/sub | `backend/src/engine/pubsub.py` |
| Workflow -> LangGraph compile, node dispatcher, edge conditions | `backend/src/engine/orchestrator.py` |
| Graph state shape | `backend/src/engine/state.py` |
| Postgres / memory checkpointer | `backend/src/engine/checkpointer.py` |
| Executor lookup by key | `backend/src/registry/executor_registry.py` |
| Runtime presets (image, command, env) | `backend/src/registry/runtime_registry.py`, `backend/config/runtimes.yaml` |
| CLI agent lifecycle | `backend/src/executors/cli_agent_executor.py` |
| Source checkout and copy | `backend/src/tools/git_fetcher.py` |
