"""Execute indexing and retrieval with the manifest-owned Vulkan runtime."""

from __future__ import annotations

from pathlib import Path

from .indexing_pipeline import run_pending_jobs as _run_indexing_jobs
from .retrieval_service import search_image_path as _search_image_path
from .retrieval_service import search_text as _search_text


def run_pending_jobs_for_active_runtime(
    library_root: Path | str,
    max_jobs: int = 20,
):
    return _run_indexing_jobs(library_root, max_jobs=max_jobs)


def search_text_for_active_runtime(
    library_root: Path | str,
    query: str,
    top_k: int,
    request_id: str | None = None,
):
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
    return _search_image_path(
        library_root,
        image_path=image_path,
        top_k=top_k,
        request_id=request_id,
    )
