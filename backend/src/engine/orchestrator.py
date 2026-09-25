"""
Orchestrator — compiles WorkflowConfig into a LangGraph StateGraph.

The make_node_func is a thin dispatcher that resolves:
  agent → node type → executor
and delegates execution entirely to the executor.
"""
import asyncio
from typing import Callable, Any, Dict
from langgraph.graph import StateGraph, START, END
from src.config.schema import WorkflowConfig
from src.engine.state import WorkflowState
from src.registry.executor_registry import get_executor
from src.registry.base_executor import ExecutionContext


def make_node_func(
    node_config: dict,
    agent_data: dict,
    node_type_data: dict,
    context: ExecutionContext,
) -> Callable:
    """
    Creates a LangGraph node function that delegates to the appropriate executor.

    Args:
        node_config: {id, agent_id, requires_approval} from the workflow definition
        agent_data: {name, node_type_key, params} resolved from DB
        node_type_data: {executor_key, config_schema, ...} resolved from DB
        context: Runtime dependencies (event bus, thread_id, etc.)
    """

    def node_func(state: WorkflowState) -> WorkflowState:
        try:
            executor_key = node_type_data["executor_key"]
            executor = get_executor(executor_key)

            # Build the config dict the executor receives
            exec_config = {
                "id": node_config["id"],
                "type": agent_data["node_type_key"],
                "agent_name": agent_data["name"],
                "params": agent_data["params"],
            }

            print(f"Executing node: {exec_config['id']} "
                  f"(agent: {exec_config['agent_name']}, "
                  f"executor: {executor_key})")

            # Run the async executor from sync LangGraph context
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    executor.execute(exec_config, dict(state), context)
                )
            finally:
                loop.close()

            return result

        except Exception as e:
            print(f"Error executing node {node_config['id']}: {str(e)}")
            # Preserve upstream state (messages/artifacts) on failure.
            return {
                **state,
                "error": str(e),
                "status": "failed",
                "current_step": node_config["id"],
            }

    return node_func


def evaluate_condition(condition: str, state: WorkflowState) -> bool:
    """
    Evaluates an edge condition against the current workflow state.
    Supports:
      - Empty / None: Always True
      - 'success' / 'completed' / 'ok': Step completed without error
      - 'failure' / 'failed' / 'error': Step failed or has an error
      - 'approved': Step was approved
      - 'rejected': Step was rejected
      - Boolean / Python expressions: Evaluated with state variables in scope
    """
    if not condition or not condition.strip():
        return True

    cond = condition.strip()
    cond_lower = cond.lower()

    if cond_lower in ("success", "completed", "ok"):
        return state.get("status") in ("completed", "success", "ok") and not state.get("error")
    if cond_lower in ("failure", "failed", "error"):
        return state.get("status") in ("failed", "error", "failure") or bool(state.get("error"))
    if cond_lower == "approved":
        return state.get("approval_status") == "approved"
    if cond_lower == "rejected":
        return state.get("approval_status") == "rejected"

    # Expression evaluation in safe environment
    safe_globals = {"__builtins__": {}}
    safe_locals = {
        "status": state.get("status"),
        "error": state.get("error"),
        "approval_status": state.get("approval_status"),
        "current_step": state.get("current_step"),
        "artifacts": state.get("artifacts") or {},
        "messages": state.get("messages") or [],
        "state": state,
        "True": True,
        "False": False,
        "None": None,
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
    }
    try:
        res = eval(cond, safe_globals, safe_locals)
        return bool(res)
    except Exception as e:
        print(f"Condition evaluation warning for '{cond}': {e}")
        return False


def compile_workflow(
    config: WorkflowConfig,
    agents_map: Dict[str, dict],
    node_types_map: Dict[str, dict],
    context: ExecutionContext,
    checkpointer: Any = None,
):
    """
    Compiles a WorkflowConfig into a LangGraph StateGraph.

    Args:
        config: Parsed workflow configuration
        agents_map: agent_id → agent data dict from DB
        node_types_map: type_key → node type data dict from DB
        context: ExecutionContext with event_bus, thread_id, etc.
        checkpointer: LangGraph checkpointer for state persistence
    """
    graph = StateGraph(WorkflowState)
    interrupt_before = []

    # 1. Node injection
    for node in config.nodes:
        agent_data = agents_map.get(node.agent_id)
        if not agent_data:
            raise ValueError(f"Agent '{node.agent_id}' not found in registry.")

        node_type_data = node_types_map.get(agent_data["node_type_key"])
        if not node_type_data:
            raise ValueError(
                f"Node type '{agent_data['node_type_key']}' not found for "
                f"agent '{node.agent_id}'."
            )

        graph.add_node(
            node.id,
            make_node_func(node.model_dump(), agent_data, node_type_data, context),
        )

        # 2. HITL injection
        if config.hitl_enabled and node.requires_approval:
            interrupt_before.append(node.id)

    # 3. Edge injection: group edges by source node to support conditional branching and fallback
    edges_by_source: Dict[str, list] = {}
    for edge in config.edges:
        edges_by_source.setdefault(edge.from_node, []).append(edge)

    for from_node, edge_list in edges_by_source.items():
        has_condition = any(bool(e.condition and e.condition.strip()) for e in edge_list)
        if not has_condition and len(edge_list) == 1:
            graph.add_edge(from_node, edge_list[0].to_node)
        else:
            def make_router(out_edges):
                def route(state: WorkflowState):
                    for e in out_edges:
                        if not e.condition or evaluate_condition(e.condition, state):
                            return e.to_node
                    return END
                return route

            path_map = {e.to_node: e.to_node for e in edge_list}
            path_map[END] = END
            graph.add_conditional_edges(
                from_node,
                make_router(edge_list),
                path_map,
            )

    # Connect start and end nodes
    incoming_edges = {edge.to_node for edge in config.edges}
    start_nodes = [node.id for node in config.nodes if node.id not in incoming_edges]
    for node_id in start_nodes:
        graph.add_edge(START, node_id)

    outgoing_edges = {edge.from_node for edge in config.edges}
    end_nodes = [node.id for node in config.nodes if node.id not in outgoing_edges]
    for node_id in end_nodes:
        graph.add_edge(node_id, END)

    compiled_graph = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before if interrupt_before else None,
    )

    return compiled_graph
