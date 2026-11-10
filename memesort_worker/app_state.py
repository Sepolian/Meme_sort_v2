from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .app_runtime import WorkerLoopSnapshot
from .library_store import LibraryStore
from .runtime_descriptor import get_runtime_descriptor
from .runtime_manifest import load_runtime_manifest
from .ocr_backend import ocr_files_available


@dataclass
class AppStateResult:
    library_root: str
    runtime: dict[str, object]
    asset_summary: dict[str, object]
    library_status: dict[str, object]
    worker_loop: dict[str, object]
    import_task: dict[str, object]
    pending_jobs: list[dict[str, object]]
    setup: dict[str, list[str]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_app_state(
    library_root: Path | str,
    worker_loop_snapshot: WorkerLoopSnapshot | None = None,
    import_task_snapshot: dict[str, object] | None = None,
) -> AppStateResult:
    library_root_path = Path(library_root).expanduser().resolve()
    with LibraryStore(library_root_path) as store:
        library_snapshot = store.read_library_snapshot()

    manifest = load_runtime_manifest()
    missing = []
    if not all(path.is_file() for path in (manifest.llama_server_path, manifest.main_model_path, manifest.projector_path)):
        missing.append("semantic runtime")
    if not ocr_files_available():
        missing.append("OCR environment")

    return AppStateResult(
        library_root=str(library_root_path),
        runtime=get_runtime_descriptor().to_dict(),
        asset_summary=library_snapshot.asset_summary.to_dict(),
        library_status=library_snapshot.library_status.to_dict(),
        worker_loop=(
            worker_loop_snapshot.to_dict()
            if worker_loop_snapshot is not None
            else {}
        ),
        import_task=import_task_snapshot or {},
        pending_jobs=library_snapshot.pending_jobs,
        setup={"missing": missing},
    )
