from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .asset_catalog import (
    MAX_IMPORT_PATH_UTF8_BYTES,
    MAX_IMPORT_SOURCES,
    BatchAssetActionResult,
    import_folder,
    rebuild_active_indexes,
)
from .import_contracts import IndexingPolicy
from .import_controller import ImportBatchConflictError, ImportTerminalOutcome


class WorkerLoop(Protocol):
    def resume(self) -> None:
        ...

    def snapshot(self):
        ...


class ImportTaskController(Protocol):
    def snapshot(self):
        ...

    def start(
        self,
        sources: Sequence[Path | str],
        on_terminal: Callable[[ImportTerminalOutcome], None] | None = None,
    ):
        ...


class RuntimeGate(Protocol):
    def is_ready_for_indexing(self) -> tuple[bool, str]:
        ...


class ImportRequestError(ValueError):
    """A malformed or ambiguous Import Batch start request."""


def parse_import_start_request(
    payload: object,
) -> tuple[list[str], IndexingPolicy]:
    """Parse canonical or legacy Import Batch start payloads strictly.

    The canonical payload is ``{"sources": [...], "indexing_policy": "..."}``.
    The legacy single-path payload is ``{"path": "...", "start_indexing": bool}``
    and maps ``false`` to ``never`` and ``true`` to ``required``.
    """
    if not isinstance(payload, dict):
        raise ImportRequestError("Import start request must be a JSON object.")

    has_sources = "sources" in payload
    has_path = "path" in payload
    if has_sources == has_path:
        raise ImportRequestError(
            "Import start request must provide exactly one of sources or path."
        )

    if has_sources:
        unknown_keys = set(payload) - {"sources", "indexing_policy"}
        if unknown_keys:
            raise ImportRequestError(
                "Canonical Import start requests accept only sources and "
                "indexing_policy."
            )
        sources = payload["sources"]
        if not isinstance(sources, list):
            raise ImportRequestError("Import sources must be a JSON array.")
        if not 1 <= len(sources) <= MAX_IMPORT_SOURCES:
            raise ImportRequestError(
                f"An Import Batch requires 1 to {MAX_IMPORT_SOURCES} sources."
            )
        if any(not isinstance(source, str) for source in sources):
            raise ImportRequestError("Every Import Source must be a string.")
        validated_sources = [
            _validate_import_source_path(source) for source in sources
        ]
        if "indexing_policy" not in payload:
            raise ImportRequestError(
                "An Import Batch start request requires indexing_policy."
            )
        return validated_sources, _parse_indexing_policy(payload["indexing_policy"])

    unknown_keys = set(payload) - {"path", "start_indexing"}
    if unknown_keys:
        raise ImportRequestError(
            "Legacy Import start requests accept only path and start_indexing."
        )
    path = payload["path"]
    if not isinstance(path, str):
        raise ImportRequestError("Import path must be a string.")
    source = _validate_import_source_path(path)
    if "start_indexing" in payload:
        start_indexing = payload["start_indexing"]
        if not isinstance(start_indexing, bool):
            raise ImportRequestError(
                "start_indexing must be a real JSON boolean."
            )
    else:
        start_indexing = False
    return [source], (
        IndexingPolicy.REQUIRED if start_indexing else IndexingPolicy.NEVER
    )


def import_and_start_indexing(
    library_root: Path | str,
    import_path: Path | str,
    worker_loop: WorkerLoop,
    runtime: RuntimeGate,
) -> dict[str, object]:
    """Authorize the runtime, import synchronously, then resume the worker."""
    runtime_ready, runtime_message = runtime.is_ready_for_indexing()
    if not runtime_ready:
        raise ValueError(runtime_message)
    import_result = import_folder(library_root, import_path)
    worker_loop.resume()
    return {
        "import_result": import_result.to_dict(),
        "worker_loop": worker_loop.snapshot().to_dict(),
    }


def start_import_batch(
    library_root: Path | str,
    sources: Sequence[str],
    import_controller: ImportTaskController,
    worker_loop: WorkerLoop,
    runtime: RuntimeGate,
    indexing_policy: IndexingPolicy,
):
    """Start one background Import Batch with the requested Indexing Policy.

    Required rejects before the batch when the Pinned Runtime is not
    authorized. If-ready always imports but records authorization at batch
    start and wakes the Worker only when jobs were created under an authorized
    runtime. Cancelled batches never wake the Worker.
    """
    if not isinstance(indexing_policy, IndexingPolicy):
        raise ValueError("indexing_policy must be one of never, required, if-ready.")

    if import_controller.snapshot().running:
        raise ImportBatchConflictError(
            "An Import Batch is already running or paused."
        )

    authorized = False
    if indexing_policy in {IndexingPolicy.REQUIRED, IndexingPolicy.IF_READY}:
        runtime_ready, runtime_message = runtime.is_ready_for_indexing()
        if indexing_policy is IndexingPolicy.REQUIRED and not runtime_ready:
            raise ValueError(runtime_message)
        authorized = runtime_ready

    if not authorized:
        return import_controller.start(list(sources))

    def _resume_after_import(outcome: ImportTerminalOutcome) -> None:
        if outcome.status in {
            "completed",
            "completed_with_errors",
            "failed",
        } and outcome.jobs_created > 0:
            worker_loop.resume()

    return import_controller.start(
        list(sources),
        on_terminal=_resume_after_import,
    )


def start_background_import(
    library_root: Path | str,
    import_path: str,
    import_controller: ImportTaskController,
    worker_loop: WorkerLoop,
    runtime: RuntimeGate,
    start_indexing: bool,
):
    """Legacy single-path wrapper mapping a boolean to an Indexing Policy."""
    return start_import_batch(
        library_root,
        [import_path],
        import_controller,
        worker_loop,
        runtime,
        IndexingPolicy.REQUIRED if start_indexing else IndexingPolicy.NEVER,
    )


def _parse_indexing_policy(raw: object) -> IndexingPolicy:
    if not isinstance(raw, str):
        raise ImportRequestError("indexing_policy must be a string.")
    try:
        return IndexingPolicy(raw)
    except ValueError:
        raise ImportRequestError(
            "indexing_policy must be one of never, required, if-ready."
        ) from None


def _validate_import_source_path(source: str) -> str:
    try:
        encoded_source = source.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded_source = b""
    if (
        not source
        or not encoded_source
        or len(encoded_source) > MAX_IMPORT_PATH_UTF8_BYTES
        or any(character in source for character in ("\x00", "\r", "\n"))
    ):
        raise ImportRequestError(
            "Every Import Source path must be transport-safe UTF-8."
        )
    return source


def rebuild_assets_and_resume(
    library_root: Path | str,
    asset_ids: list[str],
    worker_loop: WorkerLoop,
) -> BatchAssetActionResult:
    """Rebuild active indexes and resume the worker only when new jobs exist."""
    result = rebuild_active_indexes(library_root, asset_ids)
    if result.reindex_jobs_created:
        worker_loop.resume()
    return result


def resolve_asset_reveal_path(
    library_root: Path,
    asset_id: str,
    target: str,
    source_path: str | None,
) -> Path:
    """Resolve the file a reveal request may open, enforcing containment rules."""
    from .library_store import LibraryStore

    with LibraryStore(library_root) as store:
        asset = store.get_asset_detail(asset_id).asset

    if target == "managed":
        candidate = (library_root / str(asset["library_path"])).resolve()
        resolved_root = library_root.resolve()
        if resolved_root not in candidate.parents and candidate != resolved_root:
            raise ValueError("Asset path is outside the library root")
        return candidate

    if target == "source":
        requested = str(source_path or "")
        source_records = asset.get("source_records") or []
        known_source_paths = {
            str(record.get("source_path"))
            for record in source_records
            if isinstance(record, dict) and record.get("source_path")
        }
        if requested not in known_source_paths:
            raise ValueError(f"Source record not found for asset {asset_id}: {requested}")
        return Path(requested).expanduser().resolve()

    raise ValueError(f"Unknown reveal target: {target}")


def resolve_log_directory(library_root: Path) -> Path:
    """Resolve the Library-owned logs directory for a native shell command."""
    resolved_root = library_root.resolve()
    logs_directory = (resolved_root / "logs").resolve()
    if resolved_root not in logs_directory.parents:
        raise ValueError("Log directory is outside the library root")
    if not logs_directory.is_dir():
        raise ValueError("Library log directory is unavailable")
    return logs_directory
