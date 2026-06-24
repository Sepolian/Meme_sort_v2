from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .embedding_backend import EmbeddingRuntimeConfig
from .library import (
    RuntimeSettings,
    get_runtime_config_for_profile,
    get_runtime_settings,
    import_folder,
    is_runtime_ready_for_indexing,
    resolve_effective_model_source,
    run_pending_jobs,
)
from .retrieval_service import search_image_path, search_text
from .runtime_service import run_first_run_flow


class WorkerLoop(Protocol):
    def resume(self) -> None:
        ...

    def snapshot(self):
        ...


def _runtime_execution_settings(
    library_root: Path | str,
) -> tuple[RuntimeSettings, str | None, EmbeddingRuntimeConfig]:
    settings = get_runtime_settings(library_root)
    effective_model_source = resolve_effective_model_source(
        settings.selected_model_key,
        settings.model_name_or_path,
    )
    runtime_config = get_runtime_config_for_profile(
        settings.selected_profile,
        model_name_or_path=effective_model_source,
    )
    return settings, effective_model_source, runtime_config


def run_jobs_for_active_runtime(
    library_root: Path | str,
    backend_name: str | None = None,
    max_jobs: int = 20,
):
    settings, effective_model_source, runtime_config = _runtime_execution_settings(library_root)
    return run_pending_jobs(
        library_root,
        backend_name=backend_name or settings.backend_name,
        model_name_or_path=effective_model_source,
        torch_dtype=runtime_config.torch_dtype,
        device=runtime_config.device,
        num_threads=runtime_config.num_threads,
        num_interop_threads=runtime_config.num_interop_threads,
        max_jobs=max_jobs,
    )


def search_text_for_active_runtime(
    library_root: Path | str,
    query: str,
    top_k: int,
    backend_name: str | None = None,
):
    settings, effective_model_source, runtime_config = _runtime_execution_settings(library_root)
    return search_text(
        library_root,
        query=query,
        top_k=top_k,
        backend_name=backend_name or settings.backend_name,
        model_name_or_path=effective_model_source,
        torch_dtype=runtime_config.torch_dtype,
        device=runtime_config.device,
        num_threads=runtime_config.num_threads,
        num_interop_threads=runtime_config.num_interop_threads,
    )


def search_image_for_active_runtime(
    library_root: Path | str,
    image_path: Path | str,
    top_k: int,
    backend_name: str | None = None,
):
    settings, effective_model_source, runtime_config = _runtime_execution_settings(library_root)
    return search_image_path(
        library_root,
        image_path=image_path,
        top_k=top_k,
        backend_name=backend_name or settings.backend_name,
        model_name_or_path=effective_model_source,
        torch_dtype=runtime_config.torch_dtype,
        device=runtime_config.device,
        num_threads=runtime_config.num_threads,
        num_interop_threads=runtime_config.num_interop_threads,
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


def run_first_run_command(
    library_root: Path | str,
    selected_profile: str,
    selected_model_key: str,
    model_name_or_path: str | None,
    import_path: str | None,
    gif_frame_count: int | None,
    backend_name: str,
    worker_loop: WorkerLoop,
) -> dict[str, object]:
    result = run_first_run_flow(
        library_root,
        selected_profile=selected_profile,
        selected_model_key=selected_model_key,
        model_name_or_path=model_name_or_path,
        import_path=import_path,
        gif_frame_count=gif_frame_count,
        backend_name=backend_name,
    )
    if result.should_resume_worker_loop:
        worker_loop.resume()
    payload = result.to_dict()
    payload["worker_loop"] = worker_loop.snapshot().to_dict()
    return payload
