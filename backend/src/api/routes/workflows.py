from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel
import uuid
import os
import asyncio

from src.config.parser import load_config
from src.engine.orchestrator import compile_workflow
from src.engine.pubsub import event_bus

router = APIRouter()

class ExecuteRequest(BaseModel):
    config_id: str

def run_workflow_sync(thread_id: str, config: dict, checkpointer, loop: asyncio.AbstractEventLoop, is_resume: bool = False):
    try:
        graph = compile_workflow(config, checkpointer)
        config_dict = {"configurable": {"thread_id": thread_id}}
        
        initial_state = None
        if not is_resume:
            initial_state = {
                "messages": [],
                "current_step": "start",
                "artifacts": {},
                "approval_status": "pending"
            }
            event_bus.publish_sync(thread_id, {"event": "start", "data": "Initializing master workflow..."}, loop=loop)
            
        stream_input = None if is_resume else initial_state
        
        for event in graph.stream(stream_input, config_dict):
            # The event is a state snapshot dict. We can pull the messages or current_step
            step_name = list(event.keys())[0] if event else "unknown"
            event_bus.publish_sync(thread_id, {"event": "agent", "data": f"Executed step: {step_name}"}, loop=loop)
            
        state = graph.get_state(config_dict)
        if state.next:
            event_bus.publish_sync(thread_id, {"event": "review", "data": f"Waiting for human approval at: {state.next}"}, loop=loop)
        else:
            event_bus.publish_sync(thread_id, {"event": "complete", "data": "Workflow finished."}, loop=loop)
            
    except Exception as e:
        event_bus.publish_sync(thread_id, {"event": "error", "data": str(e)}, loop=loop)

@router.post("/{config_id}/execute")
async def execute_workflow(config_id: str, request: Request, background_tasks: BackgroundTasks):
    thread_id = str(uuid.uuid4())
    checkpointer = request.app.state.checkpointer
    loop = asyncio.get_running_loop()
    
    # Load config (using a hardcoded path for now assuming master_workflow.yaml)
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../config/master_workflow.yaml"))
    try:
        config = load_config(config_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")
        
    background_tasks.add_task(run_workflow_sync, thread_id, config, checkpointer, loop, is_resume=False)
    
    return {"status": "started", "thread_id": thread_id}

@router.post("/{thread_id}/resume")
async def resume_workflow(thread_id: str, request: Request, background_tasks: BackgroundTasks):
    checkpointer = request.app.state.checkpointer
    loop = asyncio.get_running_loop()
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../config/master_workflow.yaml"))
    try:
        config = load_config(config_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")
        
    background_tasks.add_task(run_workflow_sync, thread_id, config, checkpointer, loop, is_resume=True)
    return {"status": "resumed", "thread_id": thread_id}

@router.get("/{thread_id}/status")
async def get_workflow_status(thread_id: str, request: Request):
    checkpointer = request.app.state.checkpointer
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../config/master_workflow.yaml"))
    try:
        config = load_config(config_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")
        
    graph = compile_workflow(config, checkpointer)
    config_dict = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config_dict)
    
    if not state.values:
        return {"thread_id": thread_id, "status": "not_found", "current_step": None}
        
    status = "running"
    if state.next:
        status = "paused"
    elif state.created_at and not state.next:
        status = "completed"
        
    current_step = state.values.get("current_step") if state.values else None
    
    return {"thread_id": thread_id, "status": status, "current_step": current_step}
