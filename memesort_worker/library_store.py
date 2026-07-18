from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Iterator

import numpy as np

from . import library
from . import job_queue
from . import ocr_artifacts
from .semantic_retrieval import AssetEmbedding, blob_to_vector


@dataclass(frozen=True)
class ActiveIndexRecipe:
    recipe_id: str
    label: str
    output_dimension: int
    instruction_text: str
    preprocess_version: str
    gif_frame_count: int


@dataclass(frozen=True)
class LibraryReadSnapshot:
    asset_summary: library.AssetListResult
    library_status: library.LibraryStatusResult
    pending_jobs: list[dict[str, object]]


class LibraryStore:
    """Persistence interface for library state that callers should not assemble from private helpers."""

    def __init__(self, library_root: Path | str) -> None:
        init_result = library.initialize_library(library_root)
        self.library_root_path = Path(init_result.library_root)
        self._conn = library._connect(library._database_path(self.library_root_path))
        active_recipe_id = library._get_active_recipe_id(self._conn)
        recipe_row = library._get_recipe_row(self._conn, active_recipe_id)
        self.active_recipe = ActiveIndexRecipe(
            recipe_id=active_recipe_id,
            label=library._recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                library._gif_frame_count_for_recipe(recipe_row),
            ),
            output_dimension=int(recipe_row["output_dimension"]),
            instruction_text=library._instruction_text_for_key(str(recipe_row["instruction_key"])),
            preprocess_version=str(recipe_row["preprocess_version"]),
            gif_frame_count=library._gif_frame_count_for_recipe(recipe_row),
        )

    def __enter__(self) -> "LibraryStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        """Keep related projections on one SQLite read snapshot."""
        self._conn.execute("BEGIN")
        try:
            yield
        finally:
            if self._conn.in_transaction:
                self._conn.rollback()

    def read_library_snapshot(self, pending_job_limit: int = 200) -> LibraryReadSnapshot:
        with self.read_transaction():
            asset_summary = self.list_asset_summaries()
            return LibraryReadSnapshot(
                asset_summary=asset_summary,
                library_status=self.get_library_status(asset_summary),
                pending_jobs=self.list_pending_jobs(pending_job_limit),
            )

    def list_asset_summaries(self) -> library.AssetListResult:
        asset_rows = self._conn.execute(
            "SELECT id, library_path, media_type, content_hash, width, height, imported_at, updated_at "
            "FROM asset WHERE deleted_at IS NULL ORDER BY imported_at ASC, id ASC"
        ).fetchall()
        return library.AssetListResult(
            library_root=str(self.library_root_path),
            active_recipe_id=self.active_recipe.recipe_id,
            active_recipe_label=self.active_recipe.label,
            assets=[self._project_asset_summary(asset_row) for asset_row in asset_rows],
        )

    def get_library_status(
        self,
        summaries: library.AssetListResult | None = None,
    ) -> library.LibraryStatusResult:
        summaries = summaries or self.list_asset_summaries()
        asset_counts: dict[str, int] = {}
        for asset in summaries.assets:
            status = str(asset["status"])
            asset_counts[status] = asset_counts.get(status, 0) + 1
        job_rows = job_queue.collect_status_job_rows(self._conn)
        return library.LibraryStatusResult(
            library_root=str(self.library_root_path),
            active_recipe_id=summaries.active_recipe_id,
            active_recipe_label=summaries.active_recipe_label,
            asset_counts=asset_counts,
            job_counts=job_queue.count_jobs_by_status(job_rows),
            total_assets=len(summaries.assets),
            total_jobs=len(job_rows),
            recent_jobs=job_queue.project_recent_jobs(job_rows),
        )

    def list_pending_jobs(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self._conn.execute(
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

    def _project_asset_summary(self, asset_row: sqlite3.Row) -> dict[str, object]:
        asset_id = str(asset_row["id"])
        source_row = self._conn.execute(
            "SELECT source_path FROM source_record WHERE asset_id = ? ORDER BY source_path ASC LIMIT 1",
            (asset_id,),
        ).fetchone()
        source_record_count = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM source_record WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
        )
        embedding_rows = self._conn.execute(
            "SELECT recipe_id FROM embedding_item WHERE asset_id = ? AND kind = 'image'",
            (asset_id,),
        ).fetchall()
        job_rows = job_queue.collect_asset_job_rows(self._conn, asset_id)
        thumbnail_row = self._conn.execute(
            "SELECT path FROM rendition WHERE asset_id = ? AND kind = 'thumbnail' LIMIT 1",
            (asset_id,),
        ).fetchone()
        thumbnail_path = str(thumbnail_row["path"]) if thumbnail_row is not None else None
        return {
            "asset_id": asset_id,
            "library_path": str(asset_row["library_path"]),
            "library_url": f"/media/{str(asset_row['library_path'])}",
            "thumbnail_url": f"/media/{thumbnail_path}" if thumbnail_path else None,
            "media_type": str(asset_row["media_type"]),
            "content_hash": str(asset_row["content_hash"]),
            "width": int(asset_row["width"]) if asset_row["width"] is not None else None,
            "height": int(asset_row["height"]) if asset_row["height"] is not None else None,
            "imported_at": str(asset_row["imported_at"]),
            "updated_at": str(asset_row["updated_at"]),
            "source_record_count": source_record_count,
            "source_records": ([{"source_path": str(source_row["source_path"])}] if source_row else []),
            "status": self._project_asset_status(embedding_rows, job_rows),
        }

    def _project_asset_status(
        self,
        embeddings: list[sqlite3.Row],
        jobs: list[sqlite3.Row],
    ) -> str:
        active_recipe_id = self.active_recipe.recipe_id
        if any(str(row["recipe_id"]) == active_recipe_id for row in embeddings):
            return "indexed"
        active_jobs = [
            row
            for row in jobs
            if str(row["type"]) == "embed_asset"
            and str(row["recipe_id"]) == active_recipe_id
        ]
        has_stale_embeddings = any(
            str(row["recipe_id"]) != active_recipe_id for row in embeddings
        )
        if any(str(row["status"]) == "failed" for row in active_jobs):
            return "failed"
        if any(str(row["status"]) in {"pending", "running"} for row in active_jobs):
            return "reindex_pending" if has_stale_embeddings else "pending_initial_index"
        return "stale_only" if has_stale_embeddings else "missing_index"

    def get_worker_state_json(self, key: str) -> dict[str, object] | None:
        return library._get_worker_state_json(self._conn, key)

    def set_worker_state_json(self, key: str, payload: dict[str, object]) -> None:
        with self._conn:
            library._set_worker_state_json(self._conn, key, payload)

    def list_active_embeddings(
        self,
        asset_id_to_exclude: str | None = None,
    ) -> list[AssetEmbedding]:
        query = """
            SELECT ei.asset_id, ei.vector_dim, ei.vector_blob, ei.source_ref, a.library_path, a.media_type, a.content_hash
            FROM embedding_item ei
            JOIN asset a ON a.id = ei.asset_id
            WHERE ei.recipe_id = ?
              AND ei.kind = 'image'
              AND a.deleted_at IS NULL
        """
        params: list[object] = [self.active_recipe.recipe_id]
        if asset_id_to_exclude is not None:
            query += "\n              AND ei.asset_id <> ?"
            params.append(asset_id_to_exclude)
        query += "\n            ORDER BY ei.created_at ASC, ei.id ASC"
        rows = self._conn.execute(query, tuple(params)).fetchall()
        return [
            AssetEmbedding(
                asset_id=str(row["asset_id"]),
                vector=blob_to_vector(bytes(row["vector_blob"]), int(row["vector_dim"])),
                source_ref=str(row["source_ref"]) if row["source_ref"] else None,
                library_path=str(row["library_path"]),
                media_type=str(row["media_type"]),
                content_hash=str(row["content_hash"]),
            )
            for row in rows
        ]

    def list_asset_embedding_vectors(self, asset_id: str) -> list[np.ndarray]:
        rows = self._conn.execute(
            """
            SELECT vector_blob, vector_dim
            FROM embedding_item
            WHERE asset_id = ?
              AND recipe_id = ?
              AND kind = 'image'
            """,
            (asset_id, self.active_recipe.recipe_id),
        ).fetchall()
        return [
            blob_to_vector(bytes(row["vector_blob"]), int(row["vector_dim"]))
            for row in rows
        ]

    def collect_ocr_search_results(self, query: str, limit: int) -> list[dict[str, object]]:
        rows = ocr_artifacts.collect_ocr_search_rows(self._conn, query, limit)
        return ocr_artifacts.project_ocr_search_rows(query, rows)
