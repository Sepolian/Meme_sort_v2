from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import asset_catalog
from . import asset_preprocessing
from . import ocr_artifacts
from . import job_queue
from .semantic_retrieval import scan_duplicate_vector_rows
from .recipe_provider import RuntimeRecipeProvider, PreprocessSpec, default_provider


# Re-export constants from asset_catalog for backward compatibility
DATABASE_NAME = asset_catalog.DATABASE_NAME
LIBRARY_DIRS = asset_catalog.LIBRARY_DIRS
SUPPORTED_EXTENSIONS = asset_catalog.SUPPORTED_EXTENSIONS
DEFAULT_OCR_RECIPE = ocr_artifacts.DEFAULT_OCR_RECIPE
VULKAN_PROFILE_ID = "vulkan"

# Re-export result dataclasses from asset_catalog
LibraryInitResult = asset_catalog.LibraryInitResult
ImportFolderResult = asset_catalog.ImportFolderResult
AssetMutationResult = asset_catalog.AssetMutationResult
BatchAssetActionResult = asset_catalog.BatchAssetActionResult
RetryJobsResult = asset_catalog.RetryJobsResult
DeletePendingJobsResult = asset_catalog.DeletePendingJobsResult


def __getattr__(name: str) -> object:
    """Provide backward-compatible lazy access to manifest-derived constants."""
    if name == "MANIFEST_RECIPE":
        return _get_manifest_recipe()
    if name == "INSTRUCTION_TEXT_BY_KEY":
        return _get_instruction_text_by_key()
    if name == "PREPROCESS_SPECS_BY_VERSION":
        return _get_preprocess_specs_by_version()
    if name == "DEFAULT_GIF_FRAME_COUNT":
        return _get_default_gif_frame_count()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get_provider() -> RuntimeRecipeProvider:
    """Return the default recipe provider (lazy, not at import time)."""
    return default_provider()


def _get_manifest_recipe() -> dict[str, object]:
    return dict(_get_provider().manifest_recipe)


def _get_instruction_text_by_key() -> dict[str, str]:
    return dict(_get_provider().instruction_text_by_key)


def _get_preprocess_specs_by_version() -> dict[str, dict[str, int]]:
    provider = _get_provider()
    return {
        version: {
            "still_max_side": spec.still_max_side,
            "gif_max_side": spec.gif_max_side,
        }
        for version, spec in provider.preprocess_specs_by_version.items()
    }


def _get_default_gif_frame_count() -> int:
    return _get_provider().default_gif_frame_count


# Read-only result dataclasses (not in asset_catalog)
@dataclass
class AssetListResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    assets: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RunJobsResult:
    library_root: str
    backend: str
    requeued_running_jobs: int
    retried_failed_jobs: int
    processed_jobs: int
    completed_jobs: int
    failed_jobs: int
    skipped_jobs: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SearchResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    query: str
    top_k: int
    results: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ImageSearchResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    query_path: str
    query_media_type: str
    top_k: int
    results: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SimilarityResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    asset_id: str
    top_k: int
    results: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class AssetDetailResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    asset: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class DuplicateScanResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    threshold: float
    pairs: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RuntimeHealthResult:
    runtime_fingerprint: str
    backend_name: str
    device: str
    gpu_name: str | None
    gpu_vendor: str | None
    gpu_vendor_id: str | None
    text_smoke_vector_dim: int | None
    image_smoke_vector_dim: int | None
    diagnostic_steps: list[dict[str, object]]
    smoke_test_ok: bool
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class LibraryStatusResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    asset_counts: dict[str, int]
    job_counts: dict[str, int]
    total_assets: int
    total_jobs: int
    recent_jobs: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SetupStateResult:
    library_root: str
    health_check_has_run: bool
    health_check_ok: bool
    health_check_summary: str
    import_source_hint: str | None
    assets_present: bool
    indexed_assets_present: bool
    pending_assets_present: bool
    active_recipe_label: str
    runtime_readiness: dict[str, object]
    checklist: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# Delegate internal helpers to asset_catalog
def _utc_now() -> str:
    return asset_catalog.utc_now()


def _resolve(path: Path | str) -> Path:
    return asset_catalog.resolve_path(path)


def _database_path(library_root: Path) -> Path:
    return asset_catalog.database_path(library_root)


def _connect(database_path: Path) -> sqlite3.Connection:
    return asset_catalog.connect(database_path)


def _create_schema(conn: sqlite3.Connection) -> None:
    return asset_catalog.create_schema(conn)


def _recipe_label(
    model_id: str,
    output_dimension: int,
    runtime_profile: str,
    gif_frame_count: int | None = None,
) -> str:
    if gif_frame_count is None:
        gif_frame_count = _get_default_gif_frame_count()
    model_name = model_id.split("/")[-1]
    base = f"{model_name} / {output_dimension}d / {runtime_profile}"
    if gif_frame_count == _get_default_gif_frame_count():
        return base
    return f"{base} / gif-f{gif_frame_count}"


def _ensure_recipe(conn: sqlite3.Connection, recipe_spec: dict[str, object]) -> str:
    return asset_catalog.ensure_recipe(conn, recipe_spec)


def _ensure_manifest_recipe(conn: sqlite3.Connection) -> str:
    return asset_catalog.ensure_manifest_recipe(conn)


def _activate_manifest_recipe(conn: sqlite3.Connection) -> tuple[str, bool, int]:
    return asset_catalog.activate_manifest_recipe(conn)


def _ensure_ocr_recipe(conn: sqlite3.Connection, recipe_spec: dict[str, object]) -> str:
    return ocr_artifacts.ensure_ocr_recipe(conn, recipe_spec)


def _ensure_default_ocr_recipe(conn: sqlite3.Connection) -> str:
    return ocr_artifacts.ensure_default_ocr_recipe(conn)


def _ensure_missing_ocr_jobs(conn: sqlite3.Connection) -> int:
    return ocr_artifacts.ensure_missing_ocr_jobs(conn, now=_utc_now())


def _get_worker_state_json(
    conn: sqlite3.Connection,
    key: str,
) -> dict[str, object] | None:
    return asset_catalog.get_worker_state_json(conn, key)


def _set_worker_state_json(
    conn: sqlite3.Connection,
    key: str,
    payload: dict[str, object],
) -> None:
    return asset_catalog.set_worker_state_json(conn, key, payload)


def _set_active_recipe_id(conn: sqlite3.Connection, recipe_id: str) -> None:
    return asset_catalog.set_active_recipe_id(conn, recipe_id)


def _get_active_recipe_id(conn: sqlite3.Connection) -> str:
    return asset_catalog.get_active_recipe_id(conn)


def _get_recipe_row(conn: sqlite3.Connection, recipe_id: str) -> sqlite3.Row:
    return asset_catalog.get_recipe_row(conn, recipe_id)


def _get_ocr_recipe_row(conn: sqlite3.Connection, ocr_recipe_id: str) -> sqlite3.Row:
    return ocr_artifacts.get_ocr_recipe_row(conn, ocr_recipe_id)


def _searchable_ocr_text(lines: list[dict[str, object]], min_confidence: float) -> str:
    return ocr_artifacts.searchable_ocr_text(lines, min_confidence)


def _store_ocr_result(
    conn: sqlite3.Connection,
    asset_id: str,
    ocr_recipe_id: str,
    ocr_output: dict[str, object],
) -> str:
    return ocr_artifacts.store_ocr_result(conn, asset_id, ocr_recipe_id, ocr_output)


def _instruction_text_for_key(instruction_key: str) -> str:
    return _get_provider().instruction_text_for_key(instruction_key)


def _preprocess_image_bytes(
    image_bytes: bytes,
    preprocess_version: str,
) -> bytes:
    provider = _get_provider()
    spec = provider.preprocess_spec_for_version(preprocess_version)
    return asset_preprocessing.preprocess_image_bytes(image_bytes, spec)


def _image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int, int]:
    return asset_preprocessing.image_dimensions_from_bytes(image_bytes)


def _safe_image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int | None, int | None]:
    return asset_preprocessing.safe_image_dimensions_from_bytes(image_bytes)


def _extract_gif_frame_bytes(
    image_bytes: bytes,
    preprocess_version: str,
    frame_count: int | None = None,
) -> list[tuple[int, bytes]]:
    provider = _get_provider()
    spec = provider.preprocess_spec_for_version(preprocess_version)
    if frame_count is None:
        frame_count = provider.default_gif_frame_count
    return asset_preprocessing.extract_gif_frame_bytes(image_bytes, spec, frame_count)


def _composite_manifest_rgb(image: "Image.Image", spec: "PreprocessSpec | None" = None) -> "Image.Image":
    if spec is None:
        spec = _get_provider().preprocess_spec_for_version(
            next(iter(_get_provider().preprocess_specs_by_version))
        )
    return asset_preprocessing.composite_rgb(image, spec)


def _gif_frame_count_for_recipe(recipe_row: sqlite3.Row) -> int:
    value = recipe_row["gif_frame_count"]
    frame_count = _get_default_gif_frame_count() if value is None else int(value)
    if frame_count <= 0:
        raise ValueError(f"Invalid gif_frame_count on recipe {recipe_row['id']}: {frame_count}")
    return frame_count


def initialize_library(
    root: Path | str,
    provider: RuntimeRecipeProvider | None = None,
) -> LibraryInitResult:
    return asset_catalog.initialize_library(root, provider)


def import_folder(
    library_root: Path | str,
    source_folder: Path | str,
    wait_for_permission: Callable[[], None] | None = None,
    provider: RuntimeRecipeProvider | None = None,
) -> ImportFolderResult:
    """Import supported files, optionally waiting at each file boundary.

    ``wait_for_permission`` is deliberately checked before a file is read or
    copied.  This lets a UI pause a long import without leaving a partly
    committed asset: a file already being processed finishes atomically, and
    the next file waits.
    """
    return asset_catalog.import_folder(
        library_root,
        source_folder,
        wait_for_permission=wait_for_permission,
        image_dimensions_fn=_safe_image_dimensions_from_bytes,
        provider=provider,
    )


def _compute_sha256(file_path: Path) -> str:
    return asset_catalog.compute_sha256(file_path)


def _upsert_source_record(conn: sqlite3.Connection, asset_id: str, source_path: str, now: str) -> str:
    return asset_catalog.upsert_source_record(conn, asset_id, source_path, now)


def _delete_asset_rows(
    conn: sqlite3.Connection,
    library_root_path: Path,
    asset_id: str,
) -> tuple[int, int, int]:
    return asset_catalog.delete_asset_rows(conn, library_root_path, asset_id)


def remove_source_record(
    library_root: Path | str,
    asset_id: str,
    source_path: str,
) -> AssetMutationResult:
    return asset_catalog.remove_source_record(library_root, asset_id, source_path)


def delete_asset(
    library_root: Path | str,
    asset_id: str,
) -> AssetMutationResult:
    return asset_catalog.delete_asset(library_root, asset_id)


def delete_assets(library_root: Path | str, asset_ids: list[str]) -> BatchAssetActionResult:
    return asset_catalog.delete_assets(library_root, asset_ids)


def rebuild_active_indexes(library_root: Path | str, asset_ids: list[str]) -> BatchAssetActionResult:
    return asset_catalog.rebuild_active_indexes(library_root, asset_ids)


def _normalize_batch_asset_ids(asset_ids: list[str]) -> list[str]:
    return asset_catalog.normalize_batch_asset_ids(asset_ids)


def _require_existing_asset_ids(conn: sqlite3.Connection, asset_ids: list[str]) -> None:
    return asset_catalog.require_existing_asset_ids(conn, asset_ids)


def retry_failed_jobs(library_root: Path | str) -> RetryJobsResult:
    return asset_catalog.retry_failed_jobs(library_root)


def delete_pending_jobs(library_root: Path | str, job_ids: list[str]) -> DeletePendingJobsResult:
    """Delete only queue records that have not been claimed by a worker.

    Assets and generated library files are intentionally untouched.  A Job
    that becomes running after the user selected it is reported as skipped.
    """
    return asset_catalog.delete_pending_jobs(library_root, job_ids)


def _collect_asset_projection(
    conn: sqlite3.Connection,
    library_root_path: Path,
    active_recipe_id: str,
    asset_row: sqlite3.Row,
) -> dict[str, object]:
    asset_id = str(asset_row["id"])
    source_rows = conn.execute(
        """
        SELECT source_path, imported_at, last_seen_at
        FROM source_record
        WHERE asset_id = ?
        ORDER BY source_path ASC
        """,
        (asset_id,),
    ).fetchall()
    embedding_rows = conn.execute(
        """
        SELECT ei.recipe_id, ei.vector_dim, er.model_id, er.output_dimension, er.runtime_profile, er.gif_frame_count
        FROM embedding_item ei
        JOIN embedding_recipe er ON er.id = ei.recipe_id
        WHERE ei.asset_id = ?
          AND ei.kind = 'image'
        ORDER BY ei.created_at ASC, ei.id ASC
        """,
        (asset_id,),
    ).fetchall()
    job_rows = job_queue.collect_asset_job_rows(conn, asset_id)
    rendition_rows = conn.execute(
        """
        SELECT kind, path, width, height, frame_index, created_at
        FROM rendition
        WHERE asset_id = ?
        ORDER BY created_at ASC
        """,
        (asset_id,),
    ).fetchall()
    ocr_rows = ocr_artifacts.collect_asset_ocr_rows(conn, asset_id)

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
            )
            for row in embedding_rows
            if str(row["recipe_id"]) != active_recipe_id
        }
    )
    asset_status = _project_asset_status(
        active_recipe_id=active_recipe_id,
        embeddings=embedding_rows,
        jobs=job_rows,
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
        "status": asset_status,
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


def list_assets(library_root: Path | str) -> AssetListResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        active_recipe_id = _get_active_recipe_id(conn)
        recipe_row = _get_recipe_row(conn, active_recipe_id)

        asset_rows = conn.execute(
            """
            SELECT id, library_path, media_type, content_hash, width, height, imported_at, updated_at
            FROM asset
            WHERE deleted_at IS NULL
            ORDER BY imported_at ASC, id ASC
            """
        ).fetchall()

        assets: list[dict[str, object]] = []
        for asset_row in asset_rows:
            assets.append(
                _collect_asset_projection(
                    conn=conn,
                    library_root_path=library_root_path,
                    active_recipe_id=active_recipe_id,
                    asset_row=asset_row,
                )
            )

        return AssetListResult(
            library_root=str(library_root_path),
            active_recipe_id=active_recipe_id,
            active_recipe_label=_recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                _gif_frame_count_for_recipe(recipe_row),
            ),
            assets=assets,
        )
    finally:
        conn.close()


def get_asset_detail(
    library_root: Path | str,
    asset_id: str,
) -> AssetDetailResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        active_recipe_id = _get_active_recipe_id(conn)
        recipe_row = _get_recipe_row(conn, active_recipe_id)
        asset_row = conn.execute(
            """
            SELECT id, library_path, media_type, content_hash, width, height, imported_at, updated_at
            FROM asset
            WHERE id = ?
              AND deleted_at IS NULL
            """,
            (asset_id,),
        ).fetchone()
        if asset_row is None:
            raise ValueError(f"Unknown asset id: {asset_id}")

        asset = _collect_asset_projection(
            conn=conn,
            library_root_path=library_root_path,
            active_recipe_id=active_recipe_id,
            asset_row=asset_row,
        )
        return AssetDetailResult(
            library_root=str(library_root_path),
            active_recipe_id=active_recipe_id,
            active_recipe_label=_recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                _gif_frame_count_for_recipe(recipe_row),
            ),
            asset=asset,
        )
    finally:
        conn.close()


def scan_duplicate_assets(
    library_root: Path | str,
    threshold: float = 0.92,
) -> DuplicateScanResult:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")

    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        active_recipe_id = _get_active_recipe_id(conn)
        recipe_row = _get_recipe_row(conn, active_recipe_id)
        vector_rows = conn.execute(
            """
            SELECT ei.asset_id, ei.vector_dim, ei.vector_blob, ei.source_ref, a.library_path, a.media_type, a.content_hash
            FROM embedding_item ei
            JOIN asset a ON a.id = ei.asset_id
            WHERE ei.recipe_id = ?
              AND ei.kind = 'image'
              AND a.deleted_at IS NULL
            ORDER BY ei.created_at ASC, ei.id ASC
            """,
            (active_recipe_id,),
        ).fetchall()

        pairs = scan_duplicate_vector_rows(vector_rows, threshold)

        return DuplicateScanResult(
            library_root=str(library_root_path),
            active_recipe_id=active_recipe_id,
            active_recipe_label=_recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                _gif_frame_count_for_recipe(recipe_row),
            ),
            threshold=threshold,
            pairs=pairs,
        )
    finally:
        conn.close()


def get_library_status(library_root: Path | str) -> LibraryStatusResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        active_recipe_id = _get_active_recipe_id(conn)
        recipe_row = _get_recipe_row(conn, active_recipe_id)
        assets_result = list_assets(library_root_path)

        asset_counts: dict[str, int] = {}
        for asset in assets_result.assets:
            asset_counts[asset["status"]] = asset_counts.get(asset["status"], 0) + 1

        job_rows = job_queue.collect_status_job_rows(conn)
        job_counts = job_queue.count_jobs_by_status(job_rows)
        recent_jobs = job_queue.project_recent_jobs(job_rows)

        return LibraryStatusResult(
            library_root=str(library_root_path),
            active_recipe_id=active_recipe_id,
            active_recipe_label=_recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                _gif_frame_count_for_recipe(recipe_row),
            ),
            asset_counts=asset_counts,
            job_counts=job_counts,
            total_assets=len(assets_result.assets),
            total_jobs=len(job_rows),
            recent_jobs=recent_jobs,
        )
    finally:
        conn.close()


def _project_asset_status(
    active_recipe_id: str,
    embeddings: list[sqlite3.Row],
    jobs: list[sqlite3.Row],
) -> str:
    has_active_embedding = any(str(row["recipe_id"]) == active_recipe_id for row in embeddings)
    if has_active_embedding:
        return "indexed"

    active_jobs = [
        row
        for row in jobs
        if str(row["type"]) == job_queue.JobType.EMBED_ASSET.value
        and str(row["recipe_id"]) == active_recipe_id
    ]
    has_stale_embeddings = any(str(row["recipe_id"]) != active_recipe_id for row in embeddings)

    if any(str(row["status"]) == "failed" for row in active_jobs):
        return "failed"
    if any(str(row["status"]) in {"pending", "running"} for row in active_jobs):
        return "reindex_pending" if has_stale_embeddings else "pending_initial_index"
    if has_stale_embeddings:
        return "stale_only"
    return "missing_index"


def _requeue_incomplete_jobs(conn: sqlite3.Connection) -> tuple[int, int]:
    return job_queue.requeue_incomplete_jobs(conn)


def run_pending_jobs(
    library_root: Path | str,
    max_jobs: int | None = None,
) -> RunJobsResult:
    from .indexing_pipeline import run_pending_jobs as _run_pending_jobs

    return _run_pending_jobs(
        library_root,
        max_jobs=max_jobs,
    )


def search_text(
    library_root: Path | str,
    query: str,
    top_k: int = 10,
) -> SearchResult:
    from .retrieval_service import search_text as _search_text

    return _search_text(
        library_root,
        query=query,
        top_k=top_k,
    )


def search_image_path(
    library_root: Path | str,
    image_path: Path | str,
    top_k: int = 10,
) -> ImageSearchResult:
    from .retrieval_service import search_image_path as _search_image_path

    return _search_image_path(
        library_root,
        image_path=image_path,
        top_k=top_k,
    )


def find_similar_assets(
    library_root: Path | str,
    asset_id: str,
    top_k: int = 10,
) -> SimilarityResult:
    from .retrieval_service import find_similar_assets as _find_similar_assets

    return _find_similar_assets(
        library_root,
        asset_id=asset_id,
        top_k=top_k,
    )
