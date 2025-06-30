"""
SSE Progress Observer for Video Generation
Implements thread-safe observer pattern for real-time progress updates
"""
import asyncio
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SSEEventType(str, Enum):
    """SSE Event types for video generation"""
    CONNECTION_ESTABLISHED = "connection_established"
    TASK_STARTED = "task_started"
    PHASE_STARTED = "phase_started"
    PHASE_PROGRESS = "phase_progress"
    PHASE_COMPLETED = "phase_completed"
    ASSET_RENDERED = "asset_rendered"
    AUDIO_GENERATED = "audio_generated"
    VIDEO_COMPOSITION_PROGRESS = "video_composition_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    WARNING = "warning"
    KEEPALIVE = "keepalive"
    STREAM_END = "stream_end"


class VideoProgressObserver:
    """Observer for video generation progress with SSE support"""
    
    def __init__(self):
        self._observers: Dict[str, List[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._task_states: Dict[str, Dict[str, Any]] = {}
    
    async def subscribe(self, task_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a task"""
        async with self._lock:
            if task_id not in self._observers:
                self._observers[task_id] = []
                self._task_states[task_id] = {
                    "status": "pending",
                    "progress": 0,
                    "current_phase": None
                }
            
            # Create queue with reasonable size limit
            queue = asyncio.Queue(maxsize=100)
            self._observers[task_id].append(queue)
            
            logger.info(f"New subscriber for task {task_id}, total subscribers: {len(self._observers[task_id])}")
            
            # Send initial connection event
            await self._notify_single(queue, SSEEventType.CONNECTION_ESTABLISHED, {
                "task_id": task_id,
                "status": self._task_states[task_id]["status"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            return queue
    
    async def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        """Unsubscribe from task updates"""
        async with self._lock:
            if task_id in self._observers and queue in self._observers[task_id]:
                self._observers[task_id].remove(queue)
                logger.info(f"Unsubscribed from task {task_id}, remaining subscribers: {len(self._observers[task_id])}")
                
                # Clean up if no more observers
                if not self._observers[task_id]:
                    del self._observers[task_id]
    
    async def notify(self, task_id: str, event_type: SSEEventType, data: Dict[str, Any]):
        """Notify all observers for a task"""
        # Update task state based on event
        await self._update_task_state(task_id, event_type, data)
        
        # Get copy of queues to avoid holding lock during notifications
        async with self._lock:
            if task_id not in self._observers:
                return
            queues = self._observers[task_id].copy()
        
        if queues:
            # Send to all queues in parallel
            tasks = [self._notify_single(q, event_type, data) for q in queues]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Log any errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to notify queue {i} for task {task_id}: {result}")
    
    async def _notify_single(self, queue: asyncio.Queue, event_type: SSEEventType, data: Dict[str, Any]):
        """Send event to a single queue"""
        try:
            message = {
                "type": event_type.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data
            }
            
            # Try to put with timeout to avoid blocking
            await asyncio.wait_for(
                queue.put(json.dumps(message)),
                timeout=1.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"Queue full or slow consumer for event {event_type}")
        except Exception as e:
            logger.error(f"Error notifying queue: {e}")
    
    async def _update_task_state(self, task_id: str, event_type: SSEEventType, data: Dict[str, Any]):
        """Update internal task state based on events"""
        async with self._lock:
            if task_id not in self._task_states:
                self._task_states[task_id] = {
                    "status": "pending",
                    "progress": 0,
                    "current_phase": None
                }
            
            state = self._task_states[task_id]
            
            if event_type == SSEEventType.TASK_STARTED:
                state["status"] = "running"
                state["started_at"] = datetime.now(timezone.utc).isoformat()
            
            elif event_type == SSEEventType.PHASE_STARTED:
                state["current_phase"] = data.get("phase")
                state["phase_number"] = data.get("phase_number")
                state["total_phases"] = data.get("total_phases")
            
            elif event_type == SSEEventType.PHASE_PROGRESS:
                state["progress"] = data.get("percentage", 0)
            
            elif event_type == SSEEventType.TASK_COMPLETED:
                state["status"] = "completed"
                state["progress"] = 100
                state["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            elif event_type == SSEEventType.TASK_FAILED:
                state["status"] = "failed"
                state["error"] = data.get("error")
    
    async def cleanup_task(self, task_id: str, delay: int = 60):
        """Clean up task after delay"""
        await asyncio.sleep(delay)
        
        # Send stream end to all observers
        await self.notify(task_id, SSEEventType.STREAM_END, {"task_id": task_id})
        
        # Remove all observers and state
        async with self._lock:
            if task_id in self._observers:
                del self._observers[task_id]
            if task_id in self._task_states:
                del self._task_states[task_id]
        
        logger.info(f"Cleaned up task {task_id}")
    
    def get_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get current state of a task"""
        return self._task_states.get(task_id)


# Global instance
progress_observer = VideoProgressObserver()