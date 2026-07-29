from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Iterator

import numpy as np

from . import asset_catalog
from . import library
from . import job_queue
from . import ocr_artifacts
from .recipe_provider import RuntimeRecipeProvider, default_provider, resolve_gif_frame_count
from .semantic_retrieval import AssetEmbedding, blob_to_vector, scan_duplicate_vector_rows


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


def _recipe_label(
    model_id: str,
    output_dimension: int,
    runtime_profile: str,
    gif_frame_count: int | None,
    provider: RuntimeRecipeProvider | None = None,
) -> str:
    provider = provider or default_provider()
    if gif_frame_count is None:
        gif_frame_count = provider.default_gif_frame_count
    model_name = model_id.split("/")[-1]
    base = f"{model_name} / {output_dimension}d / {runtime_profile}"
    if gif_frame_count == provider.default_gif_frame_count:
        return base
    return f"{base} / gif-f{gif_frame_count}"


def _gif_frame_count_for_recipe(
    recipe_row: sqlite3.Row,
    provider: RuntimeRecipeProvider | None = None,
) -> int:
    return resolve_gif_frame_count(recipe_row["gif_frame_count"], str(recipe_row["id"]), provider)


def _project_asset_status(
    active_recipe_id: str,
    embeddings: list[sqlite3.Row],
    jobs: list[sqlite3.Row],
) -> str:
    """The single implementation of the Asset browse-status state machine."""
    if any(str(row["recipe_id"]) == active_recipe_id for row in embeddings):
        return "indexed"
    active_jobs = [
        row
        for row in jobs
        if str(row["type"]) == job_queue.JobType.EMBED_ASSET.value
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


_BULK_QUERY_CHUNK_SIZE = 500


def _chunked(values: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(values), _BULK_QUERY_CHUNK_SIZE):
        yield values[start : start + _BULK_QUERY_CHUNK_SIZE]


@dataclass
class _AssetRelatedRows:
    sources: dict[str, list[sqlite3.Row]] = field(default_factory=dict)
    embeddings: dict[str, list[sqlite3.Row]] = field(default_factory=dict)
    jobs: dict[str, list[sqlite3.Row]] = field(default_factory=dict)
    renditions: dict[str, list[sqlite3.Row]] = field(default_factory=dict)
    ocr: dict[str, list[sqlite3.Row]] = field(default_factory=dict)


def _thumbnail_path(rendition_rows: list[sqlite3.Row]) -> str | None:
    for row in rendition_rows:
        if str(row["kind"]) == "thumbnail":
            return str(row["path"])
    return None


class LibraryStore:
    """Persistence interface for library state that callers should not assemble from private helpers."""

    def __init__(
        self,
        library_root: Path | str,
        provider: RuntimeRecipeProvider | None = None,
    ) -> None:
        init_result = asset_catalog.initialize_library(library_root, provider)
        self.library_root_path = Path(init_result.library_root)
        self._conn = asset_catalog.connect(asset_catalog.database_path(self.library_root_path))
        active_recipe_id = asset_catalog.get_active_recipe_id(self._conn)
        recipe_row = asset_catalog.get_recipe_row(self._conn, active_recipe_id)
        provider = provider or default_provider()
        self._provider = provider
        self.active_recipe = ActiveIndexRecipe(
            recipe_id=active_recipe_id,
            label=_recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                _gif_frame_count_for_recipe(recipe_row, provider),
                provider,
            ),
            output_dimension=int(recipe_row["output_dimension"]),
            instruction_text=provider.instruction_text_for_key(str(recipe_row["instruction_key"])),
            preprocess_version=str(recipe_row["preprocess_version"]),
            gif_frame_count=_gif_frame_count_for_recipe(recipe_row, provider),
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
        groups = self._load_asset_related_rows(
            [str(row["id"]) for row in asset_rows], include_ocr=False
        )
        return library.AssetListResult(
            library_root=str(self.library_root_path),
            active_recipe_id=self.active_recipe.recipe_id,
            active_recipe_label=self.active_recipe.label,
            assets=[
                self._project_asset_summary(asset_row, groups) for asset_row in asset_rows
            ],
        )

    def list_assets_detailed(self) -> library.AssetListResult:
        asset_rows = self._conn.execute(
            "SELECT id, library_path, media_type, content_hash, width, height, imported_at, updated_at "
            "FROM asset WHERE deleted_at IS NULL ORDER BY imported_at ASC, id ASC"
        ).fetchall()
        groups = self._load_asset_related_rows(
            [str(row["id"]) for row in asset_rows], include_ocr=True
        )
        return library.AssetListResult(
            library_root=str(self.library_root_path),
            active_recipe_id=self.active_recipe.recipe_id,
            active_recipe_label=self.active_recipe.label,
            assets=[
                self._project_asset_detail(asset_row, groups) for asset_row in asset_rows
            ],
        )

    def get_asset_detail(self, asset_id: str) -> library.AssetDetailResult:
        asset_row = self._conn.execute(
            "SELECT id, library_path, media_type, content_hash, width, height, imported_at, updated_at "
            "FROM asset WHERE id = ? AND deleted_at IS NULL",
            (asset_id,),
        ).fetchone()
        if asset_row is None:
            raise ValueError(f"Unknown asset id: {asset_id}")
        groups = self._load_asset_related_rows([str(asset_row["id"])], include_ocr=True)
        return library.AssetDetailResult(
            library_root=str(self.library_root_path),
            active_recipe_id=self.active_recipe.recipe_id,
            active_recipe_label=self.active_recipe.label,
            asset=self._project_asset_detail(asset_row, groups),
        )

    def scan_duplicate_assets(self, threshold: float = 0.92) -> library.DuplicateScanResult:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        vector_rows = self._conn.execute(
            """
            SELECT ei.asset_id, ei.vector_dim, ei.vector_blob, ei.source_ref, a.library_path, a.media_type, a.content_hash
            FROM embedding_item ei
            JOIN asset a ON a.id = ei.asset_id
            WHERE ei.recipe_id = ?
              AND ei.kind = 'image'
              AND a.deleted_at IS NULL
            ORDER BY ei.created_at ASC, ei.id ASC
            """,
            (self.active_recipe.recipe_id,),
        ).fetchall()
        return library.DuplicateScanResult(
            library_root=str(self.library_root_path),
            active_recipe_id=self.active_recipe.recipe_id,
            active_recipe_label=self.active_recipe.label,
            threshold=threshold,
            pairs=scan_duplicate_vector_rows(vector_rows, threshold),
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

    def _load_asset_related_rows(
        self,
        asset_ids: list[str],
        include_ocr: bool,
    ) -> "_AssetRelatedRows":
        groups = _AssetRelatedRows()
        for chunk in _chunked(asset_ids):
            placeholders = ", ".join("?" for _ in chunk)
            for row in self._conn.execute(
                f"""
                SELECT asset_id, source_path, imported_at, last_seen_at
                FROM source_record
                WHERE asset_id IN ({placeholders})
                ORDER BY source_path ASC
                """,
                tuple(chunk),
            ):
                groups.sources.setdefault(str(row["asset_id"]), []).append(row)
            for row in self._conn.execute(
                f"""
                SELECT ei.asset_id, ei.recipe_id, ei.vector_dim, er.model_id,
                       er.output_dimension, er.runtime_profile, er.gif_frame_count
                FROM embedding_item ei
                JOIN embedding_recipe er ON er.id = ei.recipe_id
                WHERE ei.asset_id IN ({placeholders})
                  AND ei.kind = 'image'
                ORDER BY ei.created_at ASC, ei.id ASC
                """,
                tuple(chunk),
            ):
                groups.embeddings.setdefault(str(row["asset_id"]), []).append(row)
            for row in job_queue.collect_job_rows_for_assets(self._conn, chunk):
                groups.jobs.setdefault(str(row["asset_id"]), []).append(row)
            for row in self._conn.execute(
                f"""
                SELECT asset_id, kind, path, width, height, frame_index, created_at
                FROM rendition
                WHERE asset_id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                tuple(chunk),
            ):
                groups.renditions.setdefault(str(row["asset_id"]), []).append(row)
            if include_ocr:
                for row in ocr_artifacts.collect_ocr_rows_for_assets(self._conn, chunk):
                    groups.ocr.setdefault(str(row["asset_id"]), []).append(row)
        return groups

    def _project_asset_summary(
        self,
        asset_row: sqlite3.Row,
        groups: "_AssetRelatedRows",
    ) -> dict[str, object]:
        asset_id = str(asset_row["id"])
        source_rows = groups.sources.get(asset_id, [])
        embedding_rows = groups.embeddings.get(asset_id, [])
        job_rows = groups.jobs.get(asset_id, [])
        thumbnail_path = _thumbnail_path(groups.renditions.get(asset_id, []))
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
            "source_record_count": len(source_rows),
            "source_records": (
                [{"source_path": str(source_rows[0]["source_path"])}] if source_rows else []
            ),
            "status": _project_asset_status(
                self.active_recipe.recipe_id, embedding_rows, job_rows
            ),
        }

    def _project_asset_detail(
        self,
        asset_row: sqlite3.Row,
        groups: "_AssetRelatedRows",
    ) -> dict[str, object]:
        asset_id = str(asset_row["id"])
        active_recipe_id = self.active_recipe.recipe_id
        source_rows = groups.sources.get(asset_id, [])
        embedding_rows = groups.embeddings.get(asset_id, [])
        job_rows = groups.jobs.get(asset_id, [])
        rendition_rows = groups.renditions.get(asset_id, [])
        ocr_rows = groups.ocr.get(asset_id, [])

        source_records = [
            {
                "source_path": str(source_row["source_path"]),
                "imported_at": str(source_row["imported_at"]),
                "last_seen_at": str(source_row["last_seen_at"]) if source_row["last_seen_at"] else None,
            }
            for source_row in source_rows
        ]
        indexed_recipe_labels = sorted(
            {
                _recipe_label(
                    str(row["model_id"]),
                    int(row["output_dimension"]),
                    str(row["runtime_profile"]),
                    int(row["gif_frame_count"]) if row["gif_frame_count"] is not None else None,
                    self._provider,
                )
                for row in embedding_rows
            }
        )
        stale_recipe_labels = sorted(
            {
                _recipe_label(
                    str(row["model_id"]),
                    int(row["output_dimension"]),
                    str(row["runtime_profile"]),
                    int(row["gif_frame_count"]) if row["gif_frame_count"] is not None else None,
                    self._provider,
                )
                for row in embedding_rows
                if str(row["recipe_id"]) != active_recipe_id
            }
        )
        thumbnail_url = None
        rendition_payloads = []
        for rendition_row in rendition_rows:
            path = str(rendition_row["path"])
            if str(rendition_row["kind"]) == "thumbnail":
                thumbnail_url = f"/media/{path}"
            rendition_payloads.append(
                {
                    "kind": str(rendition_row["kind"]),
                    "path": path,
                    "url": f"/media/{path}",
                    "width": int(rendition_row["width"]) if rendition_row["width"] is not None else None,
                    "height": int(rendition_row["height"]) if rendition_row["height"] is not None else None,
                    "frame_index": int(rendition_row["frame_index"]) if rendition_row["frame_index"] is not None else None,
                    "created_at": str(rendition_row["created_at"]),
                }
            )
        ocr_payloads = ocr_artifacts.project_asset_ocr_results(ocr_rows)

        return {
            "asset_id": asset_id,
            "library_path": str(asset_row["library_path"]),
            "library_url": f"/media/{str(asset_row['library_path'])}",
            "thumbnail_url": thumbnail_url,
            "media_type": str(asset_row["media_type"]),
            "content_hash": str(asset_row["content_hash"]),
            "width": int(asset_row["width"]) if asset_row["width"] is not None else None,
            "height": int(asset_row["height"]) if asset_row["height"] is not None else None,
            "imported_at": str(asset_row["imported_at"]),
            "updated_at": str(asset_row["updated_at"]),
            "source_record_count": len(source_records),
            "source_records": source_records,
            "indexed_recipe_labels": indexed_recipe_labels,
            "stale_recipe_labels": stale_recipe_labels,
            "status": _project_asset_status(active_recipe_id, embedding_rows, job_rows),
            "ocr_status": "ready" if ocr_payloads else "missing",
            "ocr_results": ocr_payloads,
            "renditions": rendition_payloads,
            "jobs": [
                {
                    "job_id": str(job_row["id"]),
                    "type": str(job_row["type"]),
                    "status": str(job_row["status"]),
                    "recipe_id": str(job_row["recipe_id"]) if job_row["recipe_id"] else None,
                    "attempt_count": int(job_row["attempt_count"]),
                }
                for job_row in job_rows
            ],
        }

    def get_worker_state_json(self, key: str) -> dict[str, object] | None:
        return asset_catalog.get_worker_state_json(self._conn, key)

    def set_worker_state_json(self, key: str, payload: dict[str, object]) -> None:
        with self._conn:
            asset_catalog.set_worker_state_json(self._conn, key, payload)

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
