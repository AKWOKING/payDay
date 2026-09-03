import pytest
from payday.services.task_queue import ResilientTaskQueue, TaskStatus


@pytest.mark.asyncio
async def test_task_queue_crash_and_recovery():
    """
    Background Task Queue Failure & Recovery Test:
    1. Enqueues 5 notification and audit tasks.
    2. Executes 2 tasks successfully.
    3. Simulates worker sudden termination/crash while remaining tasks are in-flight.
    4. Restarts worker process and triggers recovery.
    5. Confirms worker resumes, consumes unacknowledged tasks, and completes with zero duplicate events.
    """
    queue = ResilientTaskQueue()
    execution_log = []

    async def sample_handler(payload):
        execution_log.append(payload["item_id"])
        return {"status": "SUCCESS", "item_id": payload["item_id"]}

    queue.register_handler("DISPATCH_NOTIFICATION", sample_handler)
    queue.register_handler("AUDIT_LOG_ARCHIVE", sample_handler)

    # 1. Enqueue 5 tasks
    tids = []
    for i in range(1, 6):
        task_type = "DISPATCH_NOTIFICATION" if i % 2 == 0 else "AUDIT_LOG_ARCHIVE"
        tid = queue.enqueue(task_type, {"item_id": f"item-{i}"})
        tids.append(tid)

    # 2. Execute first 2 tasks
    await queue.fetch_and_execute_next()
    await queue.fetch_and_execute_next()
    assert len(execution_log) == 2
    assert queue.get_task(tids[0]).status == TaskStatus.COMPLETED
    assert queue.get_task(tids[1]).status == TaskStatus.COMPLETED

    # 3. Simulate Worker Crash
    queue.simulate_worker_crash()
    # Attempting execution while crashed returns None
    assert await queue.fetch_and_execute_next() is None

    # 4. Restart Worker & Trigger Recovery
    queue.restart_worker_and_recover()

    # 5. Process remaining pending tasks
    processed_count = await queue.process_all_pending()
    assert processed_count == 3

    # Verify all 5 tasks reached COMPLETED state
    for tid in tids:
        task = queue.get_task(tid)
        assert task.status == TaskStatus.COMPLETED

    # Verify each item executed exactly once (no duplicate events)
    assert len(execution_log) == 5
    assert len(set(execution_log)) == 5
