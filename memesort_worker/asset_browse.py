"""Read Asset summaries and Library status through the Library Store interface."""

from __future__ import annotations

from pathlib import Path

from . import library
from .library_store import LibraryReadSnapshot, LibraryStore


def list_asset_summaries(library_root: Path | str) -> library.AssetListResult:
    with LibraryStore(library_root) as store:
        return store.list_asset_summaries()


def read_library_snapshot(
    library_root: Path | str,
    pending_job_limit: int = 200,
) -> LibraryReadSnapshot:
    with LibraryStore(library_root) as store:
        return store.read_library_snapshot(pending_job_limit)


def get_library_status(library_root: Path | str) -> library.LibraryStatusResult:
    with LibraryStore(library_root) as store:
        return store.get_library_status()


def list_pending_jobs(
    library_root: Path | str,
    limit: int = 200,
) -> list[dict[str, object]]:
    """Return the actionable queue in oldest-first order for local debugging."""
    with LibraryStore(library_root) as store:
        return store.list_pending_jobs(limit)
