"""Execute indexing and retrieval with the selected Runtime Profile."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .embedding_backend import EmbeddingRuntimeConfig
from .indexing_pipeline import run_pending_jobs as _run_indexing_jobs
from .library_internal import (
    RuntimeSettings,
    get_runtime_config_for_profile,
    get_runtime_settings,
    resolve_effective_model_source_for_backend,
)
from .retrieval_service import search_image_path as _search_image_path
from .retrieval_service import search_text as _search_text


@dataclass(frozen=True)
class ActiveRuntime:
    """Resolved execution settings for one library's selected Runtime Profile."""

    settings: RuntimeSettings
    model_source: str | None
    embedding_config: EmbeddingRuntimeConfig

    @property
    def backend_name(self) -> str:
        return self.settings.backend_name


def resolve_active_runtime(library_root: Path | str) -> ActiveRuntime:
    settings = get_runtime_settings(library_root)
    model_source = resolve_effective_model_source_for_backend(
        settings.backend_name,
        settings.selected_model_key,
        settings.model_name_or_path,
    )
    return ActiveRuntime(
        settings=settings,
        model_source=model_source,
        embedding_config=get_runtime_config_for_profile(
            settings.selected_profile,
            model_name_or_path=model_source,
        ),
    )


def run_pending_jobs_for_active_runtime(
    library_root: Path | str,
    backend_name: str | None = None,
    max_jobs: int = 20,
):
    runtime = resolve_active_runtime(library_root)
    _reject_backend_override(backend_name, runtime.backend_name)
    return _run_indexing_jobs(
        library_root,
        backend_name=runtime.backend_name,
        model_name_or_path=runtime.model_source,
        torch_dtype=runtime.embedding_config.torch_dtype,
        device=runtime.embedding_config.device,
        num_threads=runtime.embedding_config.num_threads,
        num_interop_threads=runtime.embedding_config.num_interop_threads,
        max_jobs=max_jobs,
    )


def search_text_for_active_runtime(
    library_root: Path | str,
    query: str,
    top_k: int,
    backend_name: str | None = None,
    request_id: str | None = None,
):
    runtime = resolve_active_runtime(library_root)
    _reject_backend_override(backend_name, runtime.backend_name)
    return _search_text(
        library_root,
        query=query,
        top_k=top_k,
        backend_name=runtime.backend_name,
        model_name_or_path=runtime.model_source,
        torch_dtype=runtime.embedding_config.torch_dtype,
        device=runtime.embedding_config.device,
        num_threads=runtime.embedding_config.num_threads,
        num_interop_threads=runtime.embedding_config.num_interop_threads,
        request_id=request_id,
    )


def search_image_for_active_runtime(
    library_root: Path | str,
    image_path: Path | str,
    top_k: int,
    backend_name: str | None = None,
    request_id: str | None = None,
):
    runtime = resolve_active_runtime(library_root)
    _reject_backend_override(backend_name, runtime.backend_name)
    return _search_image_path(
        library_root,
        image_path=image_path,
        top_k=top_k,
        backend_name=runtime.backend_name,
        model_name_or_path=runtime.model_source,
        torch_dtype=runtime.embedding_config.torch_dtype,
        device=runtime.embedding_config.device,
        num_threads=runtime.embedding_config.num_threads,
        num_interop_threads=runtime.embedding_config.num_interop_threads,
        request_id=request_id,
    )


def _reject_backend_override(requested: str | None, active: str) -> None:
    if requested is not None and requested != active:
        raise ValueError(
            f"Backend override {requested!r} is not allowed; the active backend is {active}."
        )
