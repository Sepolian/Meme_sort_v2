from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .import_contracts import (
    ImportBatchError,
    ImportBatchResult,
    ImportBatchStatus,
    ImportPhase,
    ImportProgress,
)
from .library import import_sources


class ImportCancelledError(RuntimeError):
    """Raised internally when the application shuts down an active batch."""


class ImportBatchConflictError(RuntimeError):
    """Raised when a second Import Batch starts while one is active or paused."""


@dataclass(frozen=True)
class ImportTerminalOutcome:
    """One immutable terminal result delivered to an exactly-once callback."""

    status: ImportBatchStatus
    batch_id: str
    result: ImportBatchResult | None
    partial_result: ImportBatchResult | None
    error: dict[str, object] | None
    jobs_created: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ImportBatchStatus(self.status))


@dataclass
class ImportSnapshot:
    """One public Import Batch snapshot.

    Status values are ``idle``, ``scanning``, ``importing``, ``pausing``,
    ``paused``, ``completed``, ``completed_with_errors``, ``failed``, and
    ``cancelled``. Progress counters expose the documented batch progress
    without including full filesystem paths; ``current_source_name`` is a
    basename.
    """

    batch_id: str | None
    status: ImportBatchStatus
    running: bool
    paused: bool
    pause_requested: bool
    source_folder: str | None
    selected_sources: int
    effective_sources: int
    discovered_files: int
    supported_files: int
    unsupported_files: int
    reparse_points_skipped: int
    scan_failures: int
    processed_files: int
    succeeded_files: int
    failed_files: int
    new_assets: int
    duplicate_assets: int
    source_records_added: int
    source_records_refreshed: int
    jobs_created: int
    current_source_name: str | None
    started_at: float | None
    finished_at: float | None
    result: dict[str, object] | None
    partial_result: dict[str, object] | None
    error: dict[str, object] | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


ImportRunner = Callable[
    [
        Path,
        Sequence[Path | str],
        Callable[[], None],
        Callable[[ImportProgress], None] | None,
    ],
    ImportBatchResult,
]


class ImportController:
    """Own one background Import Batch with pause and shutdown semantics.

    The controller holds no database connection. It gates the synchronous
    multi-source import between scan entries and supported files, publishes
    progress snapshots, preserves partial results, and invokes one terminal
    callback for every terminal outcome.
    """

    def __init__(
        self,
        library_root: Path,
        import_runner: ImportRunner | None = None,
    ) -> None:
        self._library_root = Path(library_root).expanduser().resolve()
        self._import_runner = import_runner or _run_import
        self._condition = threading.Condition()
        self._active = False
        self._shutdown = False
        self._status = ImportBatchStatus.IDLE
        self._phase = ImportPhase.SCANNING
        self._batch_id: str | None = None
        self._setup_source_folder: str | None = None
        self._progress = ImportProgress(
            phase=ImportPhase.SCANNING,
            current_source_name=None,
        )
        self._paused = False
        self._waiting_for_permission = False
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._result: dict[str, object] | None = None
        self._partial_result: dict[str, object] | None = None
        self._error: dict[str, object] | None = None
        self._thread: threading.Thread | None = None

    def start(
        self,
        sources: Sequence[Path | str],
        on_terminal: Callable[[ImportTerminalOutcome], None] | None = None,
        *,
        source_folder: str | None = None,
    ) -> ImportSnapshot:
        if not isinstance(sources, Sequence) or isinstance(
            sources,
            (str, bytes, bytearray),
        ):
            raise ValueError("Import Sources must be provided as a sequence.")
        if not sources:
            raise ValueError("An Import Batch requires at least one source.")

        source_paths = list(sources)
        with self._condition:
            if self._shutdown:
                raise RuntimeError("The Import Controller has shut down.")
            if self._active:
                raise ImportBatchConflictError(
                    "An Import Batch is already running or paused."
                )

            self._active = True
            self._status = ImportBatchStatus.SCANNING
            self._phase = ImportPhase.SCANNING
            self._batch_id = str(uuid.uuid4())
            self._setup_source_folder = source_folder
            self._progress = ImportProgress(
                phase=ImportPhase.SCANNING,
                current_source_name=None,
                selected_sources=len(source_paths),
            )
            self._paused = False
            self._waiting_for_permission = False
            self._started_at = time.time()
            self._finished_at = None
            self._result = None
            self._partial_result = None
            self._error = None
            self._thread = threading.Thread(
                target=self._run,
                args=(source_paths, on_terminal),
                name="MemeSortImportBatch",
                daemon=True,
            )
            self._thread.start()
            return self._snapshot_locked()

    def pause(self) -> ImportSnapshot:
        with self._condition:
            if self._active and not self._paused:
                self._paused = True
                self._status = ImportBatchStatus.PAUSING
            return self._snapshot_locked()

    def resume(self) -> ImportSnapshot:
        with self._condition:
            if self._active:
                self._paused = False
                self._waiting_for_permission = False
                self._status = ImportBatchStatus(self._phase)
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

    def _run(
        self,
        sources: Sequence[Path | str],
        on_terminal: Callable[[ImportTerminalOutcome], None] | None,
    ) -> None:
        try:
            result = self._import_runner(
                self._library_root,
                sources,
                self._wait_for_permission,
                self._on_progress,
            )
            status = (
                ImportBatchStatus.COMPLETED
                if result.failure_count == 0
                else ImportBatchStatus.COMPLETED_WITH_ERRORS
            )
            self._settle_terminal(
                status,
                on_terminal,
                result=result,
                partial_result=None,
                error=None,
            )
        except ImportCancelledError as exc:
            self._settle_terminal(
                ImportBatchStatus.CANCELLED,
                on_terminal,
                result=None,
                partial_result=self._build_partial_result_from_progress(),
                error={
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )
        except ImportBatchError as exc:
            self._settle_terminal(
                ImportBatchStatus.FAILED,
                on_terminal,
                result=None,
                partial_result=(
                    exc.partial_result
                    or self._build_partial_result_from_progress()
                ),
                error=exc.to_dict(),
            )
        except Exception as exc:
            self._settle_terminal(
                ImportBatchStatus.FAILED,
                on_terminal,
                result=None,
                partial_result=self._build_partial_result_from_progress(),
                error={
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )

    def _settle_terminal(
        self,
        status: ImportBatchStatus,
        on_terminal: Callable[[ImportTerminalOutcome], None] | None,
        *,
        result: ImportBatchResult | None,
        partial_result: ImportBatchResult | None,
        error: dict[str, object] | None,
    ) -> None:
        with self._condition:
            if result is not None:
                jobs_created = result.jobs_created
            elif partial_result is not None:
                jobs_created = partial_result.jobs_created
            else:
                jobs_created = 0
            outcome = ImportTerminalOutcome(
                status=status,
                batch_id=self._batch_id or "",
                result=result,
                partial_result=partial_result,
                error=error,
                jobs_created=jobs_created,
            )
            self._status = status
            self._result = result.to_dict() if result is not None else None
            self._partial_result = (
                partial_result.to_dict() if partial_result is not None else None
            )
            self._error = error
            self._finished_at = time.time()
            self._active = False
            self._waiting_for_permission = False
            self._condition.notify_all()

        if on_terminal is not None:
            try:
                on_terminal(outcome)
            except Exception:
                pass

    def _on_progress(self, progress: ImportProgress) -> None:
        with self._condition:
            if not self._active:
                return
            self._progress = replace(
                progress,
                selected_sources=(
                    progress.selected_sources or self._progress.selected_sources
                ),
                effective_sources=(
                    progress.effective_sources or self._progress.effective_sources
                ),
            )
            self._phase = self._progress.phase
            if self._status not in {
                ImportBatchStatus.PAUSING,
                ImportBatchStatus.PAUSED,
            }:
                self._status = ImportBatchStatus(self._phase)

    def _wait_for_permission(self) -> None:
        with self._condition:
            while self._paused and not self._shutdown:
                self._waiting_for_permission = True
                self._status = ImportBatchStatus.PAUSED
                self._condition.notify_all()
                self._condition.wait()
            self._waiting_for_permission = False
            if self._shutdown:
                raise ImportCancelledError(
                    "Import stopped because the application is shutting down"
                )

    def _build_partial_result_from_progress(self) -> ImportBatchResult | None:
        with self._condition:
            if self._progress.effective_sources == 0:
                return None
            try:
                return self._progress.as_batch_result(
                    library_root=str(self._library_root),
                )
            except ValueError:
                return None

    def _snapshot_locked(self) -> ImportSnapshot:
        running = self._active
        progress = self._progress
        return ImportSnapshot(
            batch_id=self._batch_id,
            status=self._status,
            running=running,
            paused=self._waiting_for_permission and running,
            pause_requested=self._paused and running,
            source_folder=self._setup_source_folder,
            selected_sources=progress.selected_sources,
            effective_sources=progress.effective_sources,
            discovered_files=progress.discovered_files,
            supported_files=progress.supported_files,
            unsupported_files=progress.unsupported_files,
            reparse_points_skipped=progress.reparse_points_skipped,
            scan_failures=progress.scan_failures,
            processed_files=progress.processed_files,
            succeeded_files=progress.succeeded_files,
            failed_files=progress.failed_files,
            new_assets=progress.new_assets,
            duplicate_assets=progress.duplicate_assets,
            source_records_added=progress.source_records_added,
            source_records_refreshed=progress.source_records_refreshed,
            jobs_created=progress.jobs_created,
            current_source_name=progress.current_source_name,
            started_at=self._started_at,
            finished_at=self._finished_at,
            result=self._result,
            partial_result=self._partial_result,
            error=self._error,
        )


def _run_import(
    library_root: Path,
    sources: Sequence[Path | str],
    wait_for_permission: Callable[[], None],
    progress_callback: Callable[[ImportProgress], None] | None,
) -> ImportBatchResult:
    return import_sources(
        library_root,
        sources,
        wait_for_permission=wait_for_permission,
        progress_callback=progress_callback,
    )
