import asyncio
from collections import defaultdict
import json

class EventBus:
    def __init__(self):
        self.subscribers = defaultdict(list)
        
    async def subscribe(self, thread_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self.subscribers[thread_id].append(queue)
        return queue
        
    def unsubscribe(self, thread_id: str, queue: asyncio.Queue):
        if queue in self.subscribers[thread_id]:
            self.subscribers[thread_id].remove(queue)
            
    async def publish(self, thread_id: str, event_data: dict):
        for queue in self.subscribers[thread_id]:
            await queue.put(event_data)
            
    def publish_sync(self, thread_id: str, event_data: dict, loop: asyncio.AbstractEventLoop = None):
        """For publishing from synchronous threads."""
        if not loop:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        
        if loop:
            for queue in self.subscribers[thread_id]:
                loop.call_soon_threadsafe(queue.put_nowait, event_data)

event_bus = EventBus()
