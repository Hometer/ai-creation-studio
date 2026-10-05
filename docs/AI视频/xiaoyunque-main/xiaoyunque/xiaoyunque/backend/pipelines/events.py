import asyncio
import json
import threading
from collections import defaultdict
from typing import Any, AsyncIterator, Dict, Set

_subscribers: Dict[str, Set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
_subscriber_lock = threading.RLock()


def _deliver(queue, payload):
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(payload)


def publish_task_event(task_id: str, event: dict[str, Any]) -> None:
    with _subscriber_lock:
        queues = list(_subscribers.get(task_id, set()))
    if not queues:
        return
    payload = {"task_id": task_id, **event}
    for loop, queue in queues:
        try:
            loop.call_soon_threadsafe(_deliver, queue, payload)
        except RuntimeError:
            pass  # Disconnected subscriber's event loop has closed.


async def task_event_stream(task_id: str, initial_event: dict[str, Any] | None = None) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    subscriber = (asyncio.get_running_loop(), queue)
    with _subscriber_lock:
        _subscribers[task_id].add(subscriber)
    try:
        if initial_event:
            yield _format_sse(initial_event)
            if initial_event.get("status") in {"completed", "failed"}:
                status = initial_event["status"]
                yield _format_sse({**initial_event, "type": status})
                yield _terminal(status)
                return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                yield _format_sse(event)
                if event.get("type") in {"completed", "failed"}:
                    yield _terminal(event["type"])
                    return
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        with _subscriber_lock:
            _subscribers[task_id].discard(subscriber)
            if not _subscribers[task_id]:
                _subscribers.pop(task_id, None)


def _format_sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _terminal(status: str) -> str:
    name = "done" if status == "completed" else "error"
    payload = {"type": name}
    if name == "error":
        payload["error"] = {"code": "PIPELINE_FAILED", "message": "专项任务未完成，请核对保留记录"}
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
