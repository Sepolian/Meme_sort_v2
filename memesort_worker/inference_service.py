from __future__ import annotations

import contextlib
import contextvars
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Generic, Iterator, Literal, TypeVar


T = TypeVar("T")
InferencePriority = Literal["search", "background"]


class InferenceCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class InferenceRequestContext:
    priority: InferencePriority = "background"
    request_id: str | None = None


@dataclass
class _WorkItem(Generic[T]):
    operation: Callable[[], T]
    context: InferenceRequestContext
    cancelled: bool = False
    started: bool = False
    finished: threading.Event = field(default_factory=threading.Event)


_CURRENT_CONTEXT: contextvars.ContextVar[InferenceRequestContext] = (
    contextvars.ContextVar(
        "memesort_inference_request",
        default=InferenceRequestContext(),
    )
)


class InferenceScheduler:
    """Serialize llama.cpp work with strict search-over-background priority."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._search_queue: deque[_WorkItem[object]] = deque()
        self._background_queue: deque[_WorkItem[object]] = deque()
        self._running: _WorkItem[object] | None = None
        self._cancelled_request_ids: set[str] = set()

    def submit(self, operation: Callable[[], T]) -> T:
        context = _CURRENT_CONTEXT.get()
        item: _WorkItem[T] = _WorkItem(operation=operation, context=context)
        with self._condition:
            if context.request_id in self._cancelled_request_ids:
                raise InferenceCancelledError(
                    f"Inference request {context.request_id} was cancelled"
                )
            queue = (
                self._search_queue
                if context.priority == "search"
                else self._background_queue
            )
            queue.append(item)  # type: ignore[arg-type]
            self._condition.notify_all()
            while True:
                if item.cancelled:
                    self._remove_pending(item)
                    raise InferenceCancelledError(
                        f"Inference request {context.request_id} was cancelled"
                    )
                if self._running is None and self._next_item() is item:
                    self._remove_pending(item)
                    item.started = True
                    self._running = item  # type: ignore[assignment]
                    break
                self._condition.wait()

        try:
            result = operation()
        finally:
            with self._condition:
                self._running = None
                item.finished.set()
                self._condition.notify_all()

        with self._condition:
            if item.cancelled or context.request_id in self._cancelled_request_ids:
                raise InferenceCancelledError(
                    f"Inference request {context.request_id} was cancelled"
                )
        return result

    def cancel(self, request_id: str) -> bool:
        request_id = validate_request_id(request_id)
        found = False
        with self._condition:
            self._cancelled_request_ids.add(request_id)
            for item in (*self._search_queue, *self._background_queue):
                if item.context.request_id == request_id:
                    item.cancelled = True
                    found = True
            if self._running and self._running.context.request_id == request_id:
                self._running.cancelled = True
                found = True
            self._condition.notify_all()
        return found

    def finish_request(self, request_id: str) -> None:
        with self._condition:
            self._cancelled_request_ids.discard(request_id)

    def _next_item(self) -> _WorkItem[object] | None:
        if self._search_queue:
            return self._search_queue[0]
        if self._background_queue:
            return self._background_queue[0]
        return None

    def _remove_pending(self, item: _WorkItem[object]) -> None:
        for queue in (self._search_queue, self._background_queue):
            try:
                queue.remove(item)
            except ValueError:
                continue


INFERENCE_SCHEDULER = InferenceScheduler()


@contextlib.contextmanager
def search_inference_request(request_id: str) -> Iterator[None]:
    request_id = validate_request_id(request_id)
    token = _CURRENT_CONTEXT.set(
        InferenceRequestContext(priority="search", request_id=request_id)
    )
    try:
        yield
    finally:
        _CURRENT_CONTEXT.reset(token)
        INFERENCE_SCHEDULER.finish_request(request_id)


def cancel_inference_request(request_id: str) -> bool:
    return INFERENCE_SCHEDULER.cancel(request_id)


def validate_request_id(request_id: str) -> str:
    try:
        parsed = uuid.UUID(str(request_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("request_id must be a UUID") from exc
    canonical = str(parsed)
    if str(request_id).lower() != canonical:
        raise ValueError("request_id must use canonical UUID form")
    return canonical
