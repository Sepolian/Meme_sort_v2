from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .asset_catalog import BatchAssetActionResult, import_folder, rebuild_active_indexes


class WorkerLoop(Protocol):
    def resume(self) -> None:
        ...

    def snapshot(self):
        ...


class ImportTaskController(Protocol):
    def start(self, path: str, on_completed=None):
        ...


class RuntimeGate(Protocol):
    def is_ready_for_indexing(self) -> tuple[bool, str]:
        ...


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


def start_background_import(
    library_root: Path | str,
    import_path: str,
    import_controller: ImportTaskController,
    worker_loop: WorkerLoop,
    runtime: RuntimeGate,
    start_indexing: bool,
):
    """Authorize before importing; resume the worker only after a completed import."""
    if start_indexing:
        runtime_ready, runtime_message = runtime.is_ready_for_indexing()
        if not runtime_ready:
            raise ValueError(runtime_message)
    return import_controller.start(
        import_path,
        on_completed=worker_loop.resume if start_indexing else None,
    )


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
