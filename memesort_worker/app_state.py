from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .app_runtime import WorkerLoopSnapshot
from .asset_browse import get_library_status, list_asset_summaries, list_pending_jobs
from .library import (
    get_runtime_settings,
    list_model_variants,
    list_runtime_profiles,
)
from .runtime_service import get_setup_state


@dataclass
class AppStateResult:
    library_root: str
    runtime_profiles: list[dict[str, object]]
    model_variants: list[dict[str, object]]
    runtime_settings: dict[str, object]
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
    runtime_settings = get_runtime_settings(library_root_path)
    setup_state = get_setup_state(library_root_path)
    asset_summary = list_asset_summaries(library_root_path)
    library_status = get_library_status(library_root_path)

    return AppStateResult(
        library_root=str(library_root_path),
        runtime_profiles=[profile.to_dict() for profile in list_runtime_profiles()],
        model_variants=[model.to_dict() for model in list_model_variants()],
        runtime_settings=runtime_settings.to_dict(),
        setup_state=setup_state.to_dict(),
        asset_summary=asset_summary.to_dict(),
        library_status=library_status.to_dict(),
        worker_loop=(
            worker_loop_snapshot.to_dict()
            if worker_loop_snapshot is not None
            else {}
        ),
        import_task=import_task_snapshot or {},
        pending_jobs=list_pending_jobs(library_root_path),
    )
