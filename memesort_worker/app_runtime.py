from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from .indexing_pipeline import run_pending_jobs


@dataclass
class WorkerLoopSnapshot:
    running: bool
    paused: bool
    interval_seconds: float
    last_tick_started_at: float | None
    last_tick_finished_at: float | None
    last_result: dict[str, object] | None
    recent_events: list[dict[str, object]]
    event_log_path: str | None
    persisted_events: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WorkerLoopController:
    def __init__(self, library_root: Path, interval_seconds: float = 2.0) -> None:
        self._library_root = library_root
        self._interval_seconds = interval_seconds
        self._paused = True
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._last_tick_started_at: float | None = None
        self._last_tick_finished_at: float | None = None
        self._last_result: dict[str, object] | None = None
        self._recent_events: list[dict[str, object]] = []
        self._event_log_path = self._library_root / "logs" / "worker-loop.jsonl"
        self._thread = threading.Thread(target=self._run_loop, name="MemeSortWorkerLoop", daemon=True)
        self._thread.start()
        self._append_event("worker-loop-started", {"interval_seconds": interval_seconds})

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        self._append_event("worker-loop-resumed", {})
        self._wake_event.set()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        self._append_event("worker-loop-paused", {})

    def trigger_once(self) -> None:
        self._append_event("worker-loop-triggered", {})
        self._wake_event.set()

    def snapshot(self) -> WorkerLoopSnapshot:
        with self._lock:
            return WorkerLoopSnapshot(
                running=not self._stop_event.is_set(),
                paused=self._paused,
                interval_seconds=self._interval_seconds,
                last_tick_started_at=self._last_tick_started_at,
                last_tick_finished_at=self._last_tick_finished_at,
                last_result=self._last_result,
                recent_events=list(self._recent_events),
                event_log_path=str(self._event_log_path),
                persisted_events=self._read_persisted_events(limit=20),
            )

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds * 2))
        self._append_event("worker-loop-stopped", {})

    def _append_event(self, event_type: str, payload: dict[str, object]) -> None:
        event_payload = {
            "event": event_type,
            "payload": payload,
            "timestamp": time.time(),
        }
        with self._lock:
            self._recent_events.insert(0, event_payload)
            del self._recent_events[50:]
        self._persist_event(event_payload)

    def _persist_event(self, payload: dict[str, object]) -> None:
        try:
            self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        except Exception:
            return

    def _read_persisted_events(self, limit: int) -> list[dict[str, object]]:
        if limit <= 0 or not self._event_log_path.exists():
            return []

        lines = deque(maxlen=limit)
        try:
            with self._event_log_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped:
                        lines.append(stripped)
        except Exception:
            return []

        events: list[dict[str, object]] = []
        for line in reversed(lines):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                paused = self._paused
            if paused:
                self._wake_event.wait(timeout=self._interval_seconds)
                self._wake_event.clear()
                continue

            self._tick_once()
            self._wake_event.wait(timeout=self._interval_seconds)
            self._wake_event.clear()

    def _tick_once(self) -> None:
        started_at = time.time()
        with self._lock:
            self._last_tick_started_at = started_at
        self._append_event("tick-started", {})

        try:
            result = run_pending_jobs(
                self._library_root,
                max_jobs=20,
            ).to_dict()
            self._append_event("tick-finished", {"processed_jobs": result["processed_jobs"]})
        except Exception as exc:
            result = {
                "error": type(exc).__name__,
                "detail": str(exc),
            }
            self._append_event("tick-failed", result)

        finished_at = time.time()
        with self._lock:
            self._last_tick_finished_at = finished_at
            self._last_result = result
