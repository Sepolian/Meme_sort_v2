"""Read Asset summaries and Library status through one module."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import job_queue
from . import library_internal as library


def project_asset_status(active_recipe_id: str, embeddings: list[sqlite3.Row], jobs: list[sqlite3.Row]) -> str:
    if any(str(row["recipe_id"]) == active_recipe_id for row in embeddings):
        return "indexed"
    active_jobs = [row for row in jobs if str(row["type"]) == "embed_asset" and str(row["recipe_id"]) == active_recipe_id]
    has_stale_embeddings = any(str(row["recipe_id"]) != active_recipe_id for row in embeddings)
    if any(str(row["status"]) == "failed" for row in active_jobs):
        return "failed"
    if any(str(row["status"]) in {"pending", "running"} for row in active_jobs):
        return "reindex_pending" if has_stale_embeddings else "pending_initial_index"
    return "stale_only" if has_stale_embeddings else "missing_index"


def list_asset_summaries(library_root: Path | str) -> library.AssetListResult:
    init_result = library.initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = library._connect(library._database_path(library_root_path))
    try:
        return _list_asset_summaries(conn, library_root_path)
    finally:
        conn.close()


def get_library_status(library_root: Path | str) -> library.LibraryStatusResult:
    init_result = library.initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = library._connect(library._database_path(library_root_path))
    try:
        summaries = _list_asset_summaries(conn, library_root_path)
        asset_counts: dict[str, int] = {}
        for asset in summaries.assets:
            status = str(asset["status"])
            asset_counts[status] = asset_counts.get(status, 0) + 1
        job_rows = job_queue.collect_status_job_rows(conn)
        return library.LibraryStatusResult(
            library_root=str(library_root_path), active_recipe_id=summaries.active_recipe_id,
            active_recipe_label=summaries.active_recipe_label, asset_counts=asset_counts,
            job_counts=job_queue.count_jobs_by_status(job_rows), total_assets=len(summaries.assets),
            total_jobs=len(job_rows), recent_jobs=job_queue.project_recent_jobs(job_rows),
        )
    finally:
        conn.close()


def list_pending_jobs(library_root: Path | str, limit: int = 200) -> list[dict[str, object]]:
    """Return the actionable queue in oldest-first order for local debugging."""
    init_result = library.initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = library._connect(library._database_path(library_root_path))
    try:
        rows = conn.execute(
            """
            SELECT job.id, job.type, job.asset_id, job.recipe_id, job.attempt_count,
                   job.created_at, job.updated_at, asset.library_path
            FROM job
            LEFT JOIN asset ON asset.id = job.asset_id
            WHERE job.status = 'pending'
            ORDER BY job.created_at ASC, job.id ASC
            LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        ).fetchall()
        return [
            {
                "job_id": str(row["id"]),
                "type": str(row["type"]),
                "asset_id": str(row["asset_id"]) if row["asset_id"] else None,
                "asset_path": str(row["library_path"]) if row["library_path"] else None,
                "recipe_id": str(row["recipe_id"]) if row["recipe_id"] else None,
                "attempt_count": int(row["attempt_count"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def _list_asset_summaries(conn: sqlite3.Connection, library_root_path: Path) -> library.AssetListResult:
    active_recipe_id = library._get_active_recipe_id(conn)
    recipe_row = library._get_recipe_row(conn, active_recipe_id)
    asset_rows = conn.execute(
        "SELECT id, library_path, media_type, content_hash, width, height, imported_at, updated_at "
        "FROM asset WHERE deleted_at IS NULL ORDER BY imported_at ASC, id ASC"
    ).fetchall()
    return library.AssetListResult(
        library_root=str(library_root_path), active_recipe_id=active_recipe_id,
        active_recipe_label=library._recipe_label(
            str(recipe_row["model_id"]), int(recipe_row["output_dimension"]),
            str(recipe_row["runtime_profile"]), library._gif_frame_count_for_recipe(recipe_row),
        ),
        assets=[_project_asset_summary(conn, active_recipe_id, asset_row) for asset_row in asset_rows],
    )


def _project_asset_summary(conn: sqlite3.Connection, active_recipe_id: str, asset_row: sqlite3.Row) -> dict[str, object]:
    asset_id = str(asset_row["id"])
    source_row = conn.execute(
        "SELECT source_path FROM source_record WHERE asset_id = ? ORDER BY source_path ASC LIMIT 1", (asset_id,)
    ).fetchone()
    source_record_count = int(conn.execute("SELECT COUNT(*) FROM source_record WHERE asset_id = ?", (asset_id,)).fetchone()[0])
    embedding_rows = conn.execute(
        "SELECT recipe_id FROM embedding_item WHERE asset_id = ? AND kind = 'image'", (asset_id,)
    ).fetchall()
    job_rows = job_queue.collect_asset_job_rows(conn, asset_id)
    thumbnail_row = conn.execute(
        "SELECT path FROM rendition WHERE asset_id = ? AND kind = 'thumbnail' LIMIT 1", (asset_id,)
    ).fetchone()
    thumbnail_path = str(thumbnail_row["path"]) if thumbnail_row is not None else None
    return {
        "asset_id": asset_id, "library_path": str(asset_row["library_path"]),
        "library_url": f"/media/{str(asset_row['library_path'])}",
        "thumbnail_url": f"/media/{thumbnail_path}" if thumbnail_path else None,
        "media_type": str(asset_row["media_type"]), "content_hash": str(asset_row["content_hash"]),
        "width": int(asset_row["width"]) if asset_row["width"] is not None else None,
        "height": int(asset_row["height"]) if asset_row["height"] is not None else None,
        "imported_at": str(asset_row["imported_at"]), "updated_at": str(asset_row["updated_at"]),
        "source_record_count": source_record_count,
        "source_records": ([{"source_path": str(source_row["source_path"])}] if source_row else []),
        "status": project_asset_status(active_recipe_id, embedding_rows, job_rows),
    }
