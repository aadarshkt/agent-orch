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
            return {
                "error": str(e),
                "status": "failed",
                "current_step": node_config["id"],
            }

    return node_func


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

    # 3. Edge injection
    for edge in config.edges:
        if edge.condition:
            def make_router(to_node):
                def route(state: WorkflowState):
                    return to_node
                return route

            graph.add_conditional_edges(
                edge.from_node,
                make_router(edge.to_node),
                {edge.to_node: edge.to_node},
            )
        else:
            graph.add_edge(edge.from_node, edge.to_node)

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
