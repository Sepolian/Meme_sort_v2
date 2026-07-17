from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .active_runtime import (
    run_pending_jobs_for_active_runtime as _run_pending_jobs_for_active_runtime,
    search_image_for_active_runtime as _search_image_for_active_runtime,
    search_text_for_active_runtime as _search_text_for_active_runtime,
)
from .library import (
    import_folder,
    is_runtime_ready_for_indexing,
)


class WorkerLoop(Protocol):
    def resume(self) -> None:
        ...

    def snapshot(self):
        ...


def run_jobs_for_active_runtime(
    library_root: Path | str,
    max_jobs: int = 20,
):
    return _run_pending_jobs_for_active_runtime(
        library_root,
        max_jobs=max_jobs,
    )


def search_text_for_active_runtime(
    library_root: Path | str,
    query: str,
    top_k: int,
    request_id: str | None = None,
):
    return _search_text_for_active_runtime(
        library_root,
        query=query,
        top_k=top_k,
        request_id=request_id,
    )


def search_image_for_active_runtime(
    library_root: Path | str,
    image_path: Path | str,
    top_k: int,
    request_id: str | None = None,
):
    return _search_image_for_active_runtime(
        library_root,
        image_path=image_path,
        top_k=top_k,
        request_id=request_id,
    )


def import_and_start_indexing(
    library_root: Path | str,
    import_path: Path | str,
    worker_loop: WorkerLoop,
) -> dict[str, object]:
    runtime_ready, runtime_message = is_runtime_ready_for_indexing(library_root)
    if not runtime_ready:
        raise ValueError(runtime_message)
    import_result = import_folder(library_root, import_path)
    worker_loop.resume()
    return {
        "import_result": import_result.to_dict(),
        "worker_loop": worker_loop.snapshot().to_dict(),
    }

