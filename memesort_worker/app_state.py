from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .app_runtime import WorkerLoopSnapshot
from .asset_browse import read_library_snapshot
from .runtime_descriptor import get_runtime_descriptor
from .runtime_service import get_setup_state


@dataclass
class AppStateResult:
    library_root: str
    runtime: dict[str, object]
    setup_state: dict[str, object]
    asset_summary: dict[str, object]
    library_status: dict[str, object]
    worker_loop: dict[str, object]
    import_task: dict[str, object]
    pending_jobs: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_app_state(
    library_root: Path | str,
    worker_loop_snapshot: WorkerLoopSnapshot | None = None,
    import_task_snapshot: dict[str, object] | None = None,
) -> AppStateResult:
    library_root_path = Path(library_root).expanduser().resolve()
    library_snapshot = read_library_snapshot(library_root_path)
    setup_state = get_setup_state(
        library_root_path,
        assets_result=library_snapshot.asset_summary,
    )

    return AppStateResult(
        library_root=str(library_root_path),
        runtime=get_runtime_descriptor().to_dict(),
        setup_state=setup_state.to_dict(),
        asset_summary=library_snapshot.asset_summary.to_dict(),
        library_status=library_snapshot.library_status.to_dict(),
        worker_loop=(
            worker_loop_snapshot.to_dict()
            if worker_loop_snapshot is not None
            else {}
        ),
        import_task=import_task_snapshot or {},
        pending_jobs=library_snapshot.pending_jobs,
    )
