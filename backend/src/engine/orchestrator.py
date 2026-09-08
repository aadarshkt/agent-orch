from typing import Callable, Any
from langgraph.graph import StateGraph, START, END
from src.config.schema import WorkflowConfig
from src.engine.state import WorkflowState

def make_node_func(node_config) -> Callable:
    def node_func(state: WorkflowState) -> WorkflowState:
        print(f"Executing node: {node_config.id} of type: {node_config.type}")
        messages = state.get("messages", [])
        new_messages = list(messages) if messages else []
        new_messages.append({"role": "system", "content": f"Executed {node_config.id}"})
        return {"messages": new_messages, "current_step": node_config.id}
    return node_func

def compile_workflow(config: WorkflowConfig, checkpointer: Any = None):
    # Initialize the graph
    graph = StateGraph(WorkflowState)
    
    interrupt_before = []
    
    # 1. Node Injection
    for node in config.nodes:
        # Dynamically add nodes
        graph.add_node(node.id, make_node_func(node))
        
        # 2. HITL Injection
        # If workflow has hitl_enabled and node requires approval
        if config.hitl_enabled and getattr(node, "requires_approval", False):
            interrupt_before.append(node.id)
            
    # 3. Edge Injection
    for edge in config.edges:
        if edge.condition:
            # Conditional edge
            def make_router(to_node):
                def route(state: WorkflowState):
                    return to_node
                return route
            
            # For demonstration, we just route unconditionally to the target node
            graph.add_conditional_edges(
                edge.from_node, 
                make_router(edge.to_node),
                {edge.to_node: edge.to_node}
            )
        else:
            # Standard edge
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
        interrupt_before=interrupt_before if interrupt_before else None
    )
    
    return compiled_graph
