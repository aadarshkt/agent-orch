from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
import asyncio
import json
from src.engine.pubsub import event_bus

router = APIRouter()

async def event_generator(request: Request, thread_id: str):
    queue = await event_bus.subscribe(thread_id)
    
    try:
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break
                
            try:
                # Wait for next event with a timeout for keep-alive
                event_data = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield {
                    "data": json.dumps(event_data)
                }
            except asyncio.TimeoutError:
                # Keep-alive ping
                yield {
                    "data": json.dumps({"event": "ping", "data": "keep-alive"})
                }
    finally:
        event_bus.unsubscribe(thread_id, queue)

@router.get("/stream")
async def stream_events(request: Request, thread_id: str):
    return EventSourceResponse(event_generator(request, thread_id))
