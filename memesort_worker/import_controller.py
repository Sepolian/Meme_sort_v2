from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .library import ImportFolderResult, import_folder


class ImportCancelledError(RuntimeError):
    """Raised internally when the application shuts down a paused import."""


@dataclass
class ImportSnapshot:
    status: str
    running: bool
    paused: bool
    pause_requested: bool
    source_folder: str | None
    started_at: float | None
    finished_at: float | None
    result: dict[str, object] | None
    error: dict[str, str] | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


ImportRunner = Callable[[Path, Path, Callable[[], None]], ImportFolderResult]


class ImportController:
    """Own one resumable import in the local application process.

    The controller holds no database connection.  It only gates library
    imports between files, so SQLite writes remain short-lived and the normal
    ``import_folder`` compatibility entry point remains synchronous.
    """

    def __init__(
        self,
        library_root: Path,
        import_runner: ImportRunner | None = None,
    ) -> None:
        self._library_root = library_root
        self._import_runner = import_runner or _run_import
        self._condition = threading.Condition()
        self._status = "idle"
        self._source_folder: str | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._result: dict[str, object] | None = None
        self._error: dict[str, str] | None = None
        self._paused = False
        self._waiting_for_permission = False
        self._shutdown = False
        self._thread: threading.Thread | None = None

    def start(self, source_folder: Path | str, on_completed: Callable[[], None] | None = None) -> ImportSnapshot:
        source_path = Path(source_folder).expanduser().resolve()
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("An import is already running or paused")
            self._status = "running"
            self._source_folder = str(source_path)
            self._started_at = time.time()
            self._finished_at = None
            self._result = None
            self._error = None
            self._paused = False
            self._thread = threading.Thread(
                target=self._run,
                args=(source_path, on_completed),
                name="MemeSortImport",
                daemon=True,
            )
            self._thread.start()
            return self._snapshot_locked()

    def pause(self) -> ImportSnapshot:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                self._paused = True
                self._status = "pausing"
            return self._snapshot_locked()

    def resume(self) -> ImportSnapshot:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                self._paused = False
                self._waiting_for_permission = False
                self._status = "running"
                self._condition.notify_all()
            return self._snapshot_locked()

    def snapshot(self) -> ImportSnapshot:
        with self._condition:
            return self._snapshot_locked()

    def shutdown(self) -> None:
        with self._condition:
            self._shutdown = True
            self._paused = False
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def _wait_for_permission(self) -> None:
        with self._condition:
            while self._paused and not self._shutdown:
                self._waiting_for_permission = True
                self._status = "paused"
                self._condition.wait()
            self._waiting_for_permission = False
            if self._shutdown:
                raise ImportCancelledError("Import stopped because the application is shutting down")

    def _run(self, source_path: Path, on_completed: Callable[[], None] | None) -> None:
        try:
            result = self._import_runner(self._library_root, source_path, self._wait_for_permission)
            with self._condition:
                self._status = "completed"
                self._result = result.to_dict()
                self._finished_at = time.time()
            if on_completed is not None:
                on_completed()
        except ImportCancelledError as exc:
            with self._condition:
                self._status = "cancelled"
                self._error = {"error": type(exc).__name__, "detail": str(exc)}
                self._finished_at = time.time()
        except Exception as exc:
            with self._condition:
                self._status = "failed"
                self._error = {"error": type(exc).__name__, "detail": str(exc)}
                self._finished_at = time.time()

    def _snapshot_locked(self) -> ImportSnapshot:
        running = self._thread is not None and self._thread.is_alive()
        return ImportSnapshot(
            status=self._status,
            running=running,
            paused=self._waiting_for_permission and running,
            pause_requested=self._paused and running,
            source_folder=self._source_folder,
            started_at=self._started_at,
            finished_at=self._finished_at,
            result=self._result,
            error=self._error,
        )


def _run_import(library_root: Path, source_folder: Path, wait_for_permission: Callable[[], None]) -> ImportFolderResult:
    return import_folder(library_root, source_folder, wait_for_permission=wait_for_permission)
