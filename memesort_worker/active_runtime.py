"""Execute indexing and retrieval with the selected Runtime Profile."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .indexing_pipeline import run_pending_jobs as _run_indexing_jobs
from .library_internal import (
    RuntimeSettings,
    get_runtime_settings,
)
from .retrieval_service import search_image_path as _search_image_path
from .retrieval_service import search_text as _search_text


@dataclass(frozen=True)
class ActiveRuntime:
    """Canonical manifest runtime for one library."""

    settings: RuntimeSettings

    @property
    def backend_name(self) -> str:
        return self.settings.backend_name


def resolve_active_runtime(library_root: Path | str) -> ActiveRuntime:
    return ActiveRuntime(settings=get_runtime_settings(library_root))


def run_pending_jobs_for_active_runtime(
    library_root: Path | str,
    max_jobs: int = 20,
):
    resolve_active_runtime(library_root)
    return _run_indexing_jobs(library_root, max_jobs=max_jobs)


def search_text_for_active_runtime(
    library_root: Path | str,
    query: str,
    top_k: int,
    request_id: str | None = None,
):
    resolve_active_runtime(library_root)
    return _search_text(
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
    resolve_active_runtime(library_root)
    return _search_image_path(
        library_root,
        image_path=image_path,
        top_k=top_k,
        request_id=request_id,
    )
