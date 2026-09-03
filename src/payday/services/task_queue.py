import uuid
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable
from enum import Enum
from payday.core.logging import logger


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BackgroundTask:
    def __init__(
        self,
        task_id: str,
        task_type: str,
        payload: Dict[str, Any],
        status: TaskStatus = TaskStatus.PENDING,
    ):
        self.task_id = task_id
        self.task_type = task_type
        self.payload = payload
        self.status = status
        self.created_at = datetime.now(timezone.utc)
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.retries: int = 0
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None


class ResilientTaskQueue:
    """
    Simulates a production distributed task broker (e.g. Celery / ARQ / Redis)
    with crash recovery, unacknowledged message redelivery, and idempotent execution guarantees.
    """

    def __init__(self):
        self._queue: List[str] = [] # List of pending task IDs
        self._tasks: Dict[str, BackgroundTask] = {}
        self._handlers: Dict[str, Callable] = {}
        self._processed_dedup_set: set = set() # Guarantees idempotency
        self._worker_active: bool = True

    def register_handler(self, task_type: str, handler: Callable):
        self._handlers[task_type] = handler

    def enqueue(self, task_type: str, payload: Dict[str, Any], task_id: Optional[str] = None) -> str:
        tid = task_id or str(uuid.uuid4())
        task = BackgroundTask(task_id=tid, task_type=task_type, payload=payload)
        self._tasks[tid] = task
        self._queue.append(tid)
        logger.info(f"[TASK QUEUE] Enqueued task {tid} of type '{task_type}'")
        return tid

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        return self._tasks.get(task_id)

    async def fetch_and_execute_next(self) -> Optional[BackgroundTask]:
        if not self._worker_active or not self._queue:
            return None

        task_id = self._queue.pop(0)
        task = self._tasks.get(task_id)
        if not task:
            return None

        # Check idempotency: if already processed, skip duplicate execution
        if task_id in self._processed_dedup_set:
            task.status = TaskStatus.COMPLETED
            logger.info(f"[TASK QUEUE] Task {task_id} already executed; acknowledging idempotently.")
            return task

        task.status = TaskStatus.PROCESSING
        task.started_at = datetime.now(timezone.utc)

        handler = self._handlers.get(task.task_type)
        try:
            if handler:
                if asyncio.iscoroutinefunction(handler):
                    res = await handler(task.payload)
                else:
                    res = handler(task.payload)
                task.result = res if isinstance(res, dict) else {"result": str(res)}
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
            self._processed_dedup_set.add(task_id)
            logger.info(f"[TASK QUEUE] Completed task {task_id}")
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"[TASK QUEUE] Task {task_id} failed: {e}")

        return task

    def simulate_worker_crash(self):
        """Simulates worker SIGKILL mid-execution while tasks are in PROCESSING or unacknowledged."""
        self._worker_active = False
        logger.warning("[TASK QUEUE] Worker crashed! In-flight tasks left unacknowledged.")

    def restart_worker_and_recover(self) -> int:
        """
        Restarts worker and scans for orphan tasks in PROCESSING state.
        Re-queues unacknowledged tasks for guaranteed delivery.
        """
        self._worker_active = True
        recovered_count = 0

        for tid, task in self._tasks.items():
            if task.status == TaskStatus.PROCESSING and tid not in self._processed_dedup_set:
                task.status = TaskStatus.PENDING
                task.retries += 1
                if tid not in self._queue:
                    self._queue.append(tid)
                    recovered_count += 1

        logger.info(f"[TASK QUEUE] Worker restarted. Recovered {recovered_count} unacknowledged tasks.")
        return recovered_count

    async def process_all_pending(self) -> int:
        count = 0
        while self._queue and self._worker_active:
            task = await self.fetch_and_execute_next()
            if task:
                count += 1
        return count


task_queue = ResilientTaskQueue()
