"""Asset Catalog: owns Asset lifecycle, Source Records, and write transactions.

This module is the single owner of Asset import, deletion, source record
merging, and batch operations.  It exposes a narrow interface for callers
that need to create or mutate assets; read-only projections remain in
LibraryStore.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import asset_preprocessing
from . import job_queue
from . import ocr_artifacts
from .recipe_provider import default_provider


DATABASE_NAME = "library.sqlite"
LIBRARY_DIRS = (
    "originals",
    "thumbnails",
    "frames",
    "contact_sheets",
    "models",
    "runtime",
    "logs",
)

SUPPORTED_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LibraryInitResult:
    library_root: str
    database_path: str
    created_database: bool
    created_recipe_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ImportFolderResult:
    library_root: str
    source_folder: str
    discovered_files: int
    supported_files: int
    unsupported_files: int
    new_assets: int
    duplicate_assets: int
    source_records_added: int
    source_records_refreshed: int
    jobs_created: int
    active_recipe_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class AssetMutationResult:
    library_root: str
    asset_id: str
    removed_source_path: str | None
    asset_deleted: bool
    removed_source_records: int
    removed_jobs: int
    removed_renditions: int
    removed_embeddings: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class BatchAssetActionResult:
    library_root: str
    action: str
    requested_asset_ids: list[str]
    affected_asset_ids: list[str]
    skipped_running_asset_ids: list[str]
    removed_source_records: int
    removed_jobs: int
    removed_renditions: int
    removed_embeddings: int
    reindex_jobs_created: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RetryJobsResult:
    library_root: str
    retried_jobs: int
    failed_jobs_remaining: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class DeletePendingJobsResult:
    requested_job_ids: list[str]
    deleted_job_ids: list[str]
    skipped_job_ids: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Database helpers (shared with indexing_pipeline via explicit interface)
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def database_path(library_root: Path) -> Path:
    return library_root / DATABASE_NAME


def connect(database_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with library pragmas."""
    conn = sqlite3.connect(database_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS asset (
            id TEXT PRIMARY KEY,
            library_path TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            width INTEGER,
            height INTEGER,
            imported_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_content_hash_live
        ON asset (content_hash)
        WHERE deleted_at IS NULL;

        CREATE TABLE IF NOT EXISTS source_record (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
            source_path TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            last_seen_at TEXT,
            UNIQUE(asset_id, source_path)
        );

        CREATE INDEX IF NOT EXISTS ix_source_record_asset_id
        ON source_record (asset_id);

        CREATE TABLE IF NOT EXISTS rendition (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            frame_index INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_rendition_asset_id
        ON rendition (asset_id);

        CREATE TABLE IF NOT EXISTS embedding_recipe (
            id TEXT PRIMARY KEY,
            family_key TEXT NOT NULL,
            model_id TEXT NOT NULL,
            model_revision TEXT NOT NULL,
            output_dimension INTEGER NOT NULL,
            runtime_profile TEXT NOT NULL,
            preprocess_version TEXT NOT NULL,
            instruction_key TEXT NOT NULL,
            pooling_key TEXT NOT NULL,
            normalized INTEGER NOT NULL,
            gif_frame_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_embedding_recipe_identity
        ON embedding_recipe (
            model_id,
            model_revision,
            output_dimension,
            runtime_profile,
            preprocess_version,
            instruction_key,
            pooling_key,
            normalized,
            gif_frame_count
        );

        CREATE TABLE IF NOT EXISTS embedding_item (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
            recipe_id TEXT NOT NULL REFERENCES embedding_recipe(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            source_ref TEXT,
            vector_dim INTEGER NOT NULL,
            vector_blob BLOB NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_embedding_item_asset_recipe
        ON embedding_item (asset_id, recipe_id, kind);

        CREATE TABLE IF NOT EXISTS worker_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        PRAGMA user_version = 1;
        """
    )
    ocr_artifacts.create_schema(conn)
    job_queue.create_schema(conn)


# ---------------------------------------------------------------------------
# Recipe helpers
# ---------------------------------------------------------------------------


def ensure_recipe(conn: sqlite3.Connection, recipe_spec: dict[str, object]) -> str:
    row = conn.execute(
        """
        SELECT id
        FROM embedding_recipe
        WHERE model_id = ?
          AND model_revision = ?
          AND output_dimension = ?
          AND runtime_profile = ?
          AND preprocess_version = ?
          AND instruction_key = ?
          AND pooling_key = ?
          AND normalized = ?
          AND gif_frame_count = ?
        """,
        (
            recipe_spec["model_id"],
            recipe_spec["model_revision"],
            recipe_spec["output_dimension"],
            recipe_spec["runtime_profile"],
            recipe_spec["preprocess_version"],
            recipe_spec["instruction_key"],
            recipe_spec["pooling_key"],
            recipe_spec["normalized"],
            recipe_spec["gif_frame_count"],
        ),
    ).fetchone()
    if row is not None:
        return str(row["id"])

    recipe_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO embedding_recipe (
            id,
            family_key,
            model_id,
            model_revision,
            output_dimension,
            runtime_profile,
            preprocess_version,
            instruction_key,
            pooling_key,
            normalized,
            gif_frame_count,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe_id,
            recipe_spec["family_key"],
            recipe_spec["model_id"],
            recipe_spec["model_revision"],
            recipe_spec["output_dimension"],
            recipe_spec["runtime_profile"],
            recipe_spec["preprocess_version"],
            recipe_spec["instruction_key"],
            recipe_spec["pooling_key"],
            recipe_spec["normalized"],
            recipe_spec["gif_frame_count"],
            utc_now(),
        ),
    )
    return recipe_id


def ensure_manifest_recipe(conn: sqlite3.Connection) -> str:
    provider = default_provider()
    return ensure_recipe(conn, dict(provider.manifest_recipe))


def get_worker_state_json(
    conn: sqlite3.Connection,
    key: str,
) -> dict[str, object] | None:
    import json

    row = conn.execute(
        "SELECT value_json FROM worker_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(str(row["value_json"]))


def set_worker_state_json(
    conn: sqlite3.Connection,
    key: str,
    payload: dict[str, object],
) -> None:
    import json

    now = utc_now()
    conn.execute(
        """
        INSERT INTO worker_state (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(payload, sort_keys=True), now),
    )


def set_active_recipe_id(conn: sqlite3.Connection, recipe_id: str) -> None:
    set_worker_state_json(conn, "active_recipe_id", {"recipe_id": recipe_id})


def get_active_recipe_id(conn: sqlite3.Connection) -> str:
    payload = get_worker_state_json(conn, "active_recipe_id")
    if payload is None:
        recipe_id = ensure_manifest_recipe(conn)
        set_active_recipe_id(conn, recipe_id)
        return recipe_id

    recipe_id = str(payload["recipe_id"])
    existing = conn.execute(
        "SELECT id FROM embedding_recipe WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if existing is not None:
        return recipe_id

    manifest_recipe_id = ensure_manifest_recipe(conn)
    set_active_recipe_id(conn, manifest_recipe_id)
    return manifest_recipe_id


def get_recipe_row(conn: sqlite3.Connection, recipe_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, model_id, output_dimension, instruction_key, preprocess_version
             , runtime_profile
             , gif_frame_count
        FROM embedding_recipe
        WHERE id = ?
        """,
        (recipe_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown recipe id: {recipe_id}")
    return row


def activate_manifest_recipe(conn: sqlite3.Connection) -> tuple[str, bool, int]:
    provider = default_provider()
    recipe_id = ensure_manifest_recipe(conn)
    active_recipe_id = get_active_recipe_id(conn)
    state = get_worker_state_json(conn, "semantic_recipe_activation")
    already_active = (
        active_recipe_id == recipe_id
        and state is not None
        and state.get("recipe_fingerprint") == provider.recipe_fingerprint
        and state.get("recipe_id") == recipe_id
    )
    if already_active:
        return recipe_id, False, 0

    first_activation = state is None and active_recipe_id == recipe_id
    if first_activation:
        set_worker_state_json(
            conn,
            "semantic_recipe_activation",
            {
                "recipe_fingerprint": provider.recipe_fingerprint,
                "recipe_id": recipe_id,
            },
        )
        return recipe_id, False, 0

    conn.execute("DELETE FROM embedding_item")
    conn.execute(
        "DELETE FROM job WHERE type = ?",
        (job_queue.JobType.EMBED_ASSET.value,),
    )
    conn.execute("DELETE FROM embedding_recipe WHERE id != ?", (recipe_id,))
    set_active_recipe_id(conn, recipe_id)
    conn.execute("DELETE FROM worker_state WHERE key = 'last_runtime_health_check'")

    queued = 0
    now = utc_now()
    asset_rows = conn.execute(
        "SELECT id, media_type FROM asset WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    for asset_row in asset_rows:
        asset_id = str(asset_row["id"])
        queued += job_queue.enqueue_embedding(
            conn,
            asset_id=asset_id,
            recipe_id=recipe_id,
            media_type=str(asset_row["media_type"]),
            now=now,
        )
    set_worker_state_json(
        conn,
        "semantic_recipe_activation",
        {
            "recipe_fingerprint": provider.recipe_fingerprint,
            "recipe_id": recipe_id,
        },
    )
    return recipe_id, True, queued


# ---------------------------------------------------------------------------
# Asset lifecycle operations
# ---------------------------------------------------------------------------


def compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def upsert_source_record(
    conn: sqlite3.Connection, asset_id: str, source_path: str, now: str
) -> str:
    existing = conn.execute(
        """
        SELECT id
        FROM source_record
        WHERE asset_id = ?
          AND source_path = ?
        """,
        (asset_id, source_path),
    ).fetchone()
    if existing is not None:
        conn.execute(
            """
            UPDATE source_record
            SET last_seen_at = ?
            WHERE id = ?
            """,
            (now, str(existing["id"])),
        )
        return "refreshed"

    conn.execute(
        """
        INSERT INTO source_record (
            id,
            asset_id,
            source_path,
            imported_at,
            last_seen_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), asset_id, source_path, now, now),
    )
    return "added"


def delete_asset_rows(
    conn: sqlite3.Connection,
    library_root_path: Path,
    asset_id: str,
) -> tuple[int, int, int]:
    """Delete an asset and all its derived artifacts. Returns (jobs, renditions, embeddings)."""
    rendition_rows = conn.execute(
        """
        SELECT path
        FROM rendition
        WHERE asset_id = ?
        """,
        (asset_id,),
    ).fetchall()
    removed_renditions = len(rendition_rows)
    for rendition_row in rendition_rows:
        path = library_root_path / str(rendition_row["path"])
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    asset_row = conn.execute(
        """
        SELECT library_path
        FROM asset
        WHERE id = ?
        """,
        (asset_id,),
    ).fetchone()
    if asset_row is not None:
        try:
            (library_root_path / str(asset_row["library_path"])).unlink(missing_ok=True)
        except Exception:
            pass

    removed_embeddings = int(
        conn.execute(
            "SELECT COUNT(*) FROM embedding_item WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()[0]
    )
    removed_jobs = job_queue.count_asset_jobs(conn, asset_id)
    ocr_artifacts.delete_asset_ocr_results(conn, asset_id)
    conn.execute("DELETE FROM asset WHERE id = ?", (asset_id,))
    return removed_jobs, removed_renditions, removed_embeddings


def normalize_batch_asset_ids(asset_ids: list[str]) -> list[str]:
    normalized = list(
        dict.fromkeys(str(asset_id).strip() for asset_id in asset_ids if str(asset_id).strip())
    )
    if not normalized:
        raise ValueError("At least one asset id is required")
    return normalized


def require_existing_asset_ids(conn: sqlite3.Connection, asset_ids: list[str]) -> None:
    placeholders = ", ".join("?" for _ in asset_ids)
    rows = conn.execute(
        f"SELECT id FROM asset WHERE deleted_at IS NULL AND id IN ({placeholders})",
        tuple(asset_ids),
    ).fetchall()
    found_ids = {str(row["id"]) for row in rows}
    missing_ids = [asset_id for asset_id in asset_ids if asset_id not in found_ids]
    if missing_ids:
        raise ValueError(f"Unknown asset ids: {', '.join(missing_ids)}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def initialize_library(root: Path | str) -> LibraryInitResult:
    library_root = resolve_path(root)
    library_root.mkdir(parents=True, exist_ok=True)
    for relative_dir in LIBRARY_DIRS:
        (library_root / relative_dir).mkdir(parents=True, exist_ok=True)

    db_path = database_path(library_root)
    created_database = not db_path.exists()

    conn = connect(db_path)
    try:
        with conn:
            create_schema(conn)
            recipe_id, _, _ = activate_manifest_recipe(conn)
            ocr_artifacts.ensure_default_ocr_recipe(conn)
            ocr_artifacts.ensure_missing_ocr_jobs(conn, now=utc_now())
    finally:
        conn.close()

    return LibraryInitResult(
        library_root=str(library_root),
        database_path=str(db_path),
        created_database=created_database,
        created_recipe_id=recipe_id,
    )


def import_folder(
    library_root: Path | str,
    source_folder: Path | str,
    wait_for_permission: Callable[[], None] | None = None,
    image_dimensions_fn: Callable[[bytes], tuple[int | None, int | None]] | None = None,
) -> ImportFolderResult:
    """Import supported files, optionally waiting at each file boundary."""
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    source_root = resolve_path(source_folder)
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError(f"Source folder does not exist or is not a directory: {source_root}")

    discovered_files = 0
    supported_files = 0
    unsupported_files = 0
    new_assets = 0
    duplicate_assets = 0
    source_records_added = 0
    source_records_refreshed = 0
    jobs_created = 0

    conn = connect(database_path(library_root_path))
    try:
        active_recipe_id = get_active_recipe_id(conn)
        ocr_recipe_id = ocr_artifacts.ensure_default_ocr_recipe(conn)
        for file_path in sorted(source_root.rglob("*")):
            if not file_path.is_file():
                continue

            if wait_for_permission is not None:
                wait_for_permission()

            discovered_files += 1
            media_type = SUPPORTED_EXTENSIONS.get(file_path.suffix.lower())
            if media_type is None:
                unsupported_files += 1
                continue

            supported_files += 1
            content_hash = compute_sha256(file_path)
            now = utc_now()

            existing_asset = conn.execute(
                """
                SELECT id
                FROM asset
                WHERE content_hash = ?
                  AND deleted_at IS NULL
                """,
                (content_hash,),
            ).fetchone()

            if existing_asset is not None:
                duplicate_assets += 1
                with conn:
                    source_change = upsert_source_record(
                        conn=conn,
                        asset_id=str(existing_asset["id"]),
                        source_path=str(file_path),
                        now=now,
                    )
                if source_change == "added":
                    source_records_added += 1
                else:
                    source_records_refreshed += 1
                continue

            asset_id = str(uuid.uuid4())
            destination_relative = Path("originals") / f"{asset_id}{file_path.suffix.lower()}"
            destination_absolute = library_root_path / destination_relative

            try:
                shutil.copy2(file_path, destination_absolute)
            except Exception:
                if destination_absolute.exists():
                    destination_absolute.unlink(missing_ok=True)
                raise

            byte_size = destination_absolute.stat().st_size
            dimensions_fn = image_dimensions_fn or asset_preprocessing.safe_image_dimensions_from_bytes
            width, height = dimensions_fn(destination_absolute.read_bytes())
            with conn:
                conn.execute(
                    """
                    INSERT INTO asset (
                        id,
                        library_path,
                        media_type,
                        content_hash,
                        byte_size,
                        width,
                        height,
                        imported_at,
                        updated_at,
                        deleted_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        destination_relative.as_posix(),
                        media_type,
                        content_hash,
                        byte_size,
                        width,
                        height,
                        now,
                        now,
                        None,
                    ),
                )
                source_change = upsert_source_record(
                    conn=conn,
                    asset_id=asset_id,
                    source_path=str(file_path),
                    now=now,
                )
                if source_change == "added":
                    source_records_added += 1
                else:
                    source_records_refreshed += 1

                jobs_created += job_queue.enqueue_thumbnail(
                    conn=conn,
                    asset_id=asset_id,
                    now=now,
                )
                jobs_created += job_queue.enqueue_embedding(
                    conn=conn,
                    asset_id=asset_id,
                    recipe_id=active_recipe_id,
                    media_type=media_type,
                    now=now,
                )
                jobs_created += ocr_artifacts.enqueue_ocr_asset_job(
                    conn,
                    asset_id=asset_id,
                    media_type=media_type,
                    now=now,
                    ocr_recipe_id=ocr_recipe_id,
                )

            new_assets += 1
    finally:
        conn.close()

    return ImportFolderResult(
        library_root=str(library_root_path),
        source_folder=str(source_root),
        discovered_files=discovered_files,
        supported_files=supported_files,
        unsupported_files=unsupported_files,
        new_assets=new_assets,
        duplicate_assets=duplicate_assets,
        source_records_added=source_records_added,
        source_records_refreshed=source_records_refreshed,
        jobs_created=jobs_created,
        active_recipe_id=active_recipe_id,
    )


def remove_source_record(
    library_root: Path | str,
    asset_id: str,
    source_path: str,
) -> AssetMutationResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = connect(database_path(library_root_path))
    try:
        with conn:
            source_row = conn.execute(
                """
                SELECT id
                FROM source_record
                WHERE asset_id = ?
                  AND source_path = ?
                """,
                (asset_id, source_path),
            ).fetchone()
            if source_row is None:
                raise ValueError(f"Source record not found for asset {asset_id}: {source_path}")

            conn.execute(
                "DELETE FROM source_record WHERE id = ?",
                (str(source_row["id"]),),
            )
            remaining_source_records = int(
                conn.execute(
                    "SELECT COUNT(*) FROM source_record WHERE asset_id = ?",
                    (asset_id,),
                ).fetchone()[0]
            )
            if remaining_source_records > 0:
                return AssetMutationResult(
                    library_root=str(library_root_path),
                    asset_id=asset_id,
                    removed_source_path=source_path,
                    asset_deleted=False,
                    removed_source_records=1,
                    removed_jobs=0,
                    removed_renditions=0,
                    removed_embeddings=0,
                )

            removed_jobs, removed_renditions, removed_embeddings = delete_asset_rows(
                conn,
                library_root_path,
                asset_id,
            )
            return AssetMutationResult(
                library_root=str(library_root_path),
                asset_id=asset_id,
                removed_source_path=source_path,
                asset_deleted=True,
                removed_source_records=1,
                removed_jobs=removed_jobs,
                removed_renditions=removed_renditions,
                removed_embeddings=removed_embeddings,
            )
    finally:
        conn.close()


def delete_asset(
    library_root: Path | str,
    asset_id: str,
) -> AssetMutationResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = connect(database_path(library_root_path))
    try:
        with conn:
            asset_row = conn.execute(
                """
                SELECT id
                FROM asset
                WHERE id = ?
                  AND deleted_at IS NULL
                """,
                (asset_id,),
            ).fetchone()
            if asset_row is None:
                raise ValueError(f"Asset not found: {asset_id}")

            removed_source_records = int(
                conn.execute(
                    "SELECT COUNT(*) FROM source_record WHERE asset_id = ?",
                    (asset_id,),
                ).fetchone()[0]
            )
            removed_jobs, removed_renditions, removed_embeddings = delete_asset_rows(
                conn,
                library_root_path,
                asset_id,
            )
            return AssetMutationResult(
                library_root=str(library_root_path),
                asset_id=asset_id,
                removed_source_path=None,
                asset_deleted=True,
                removed_source_records=removed_source_records,
                removed_jobs=removed_jobs,
                removed_renditions=removed_renditions,
                removed_embeddings=removed_embeddings,
            )
    finally:
        conn.close()


def delete_assets(library_root: Path | str, asset_ids: list[str]) -> BatchAssetActionResult:
    requested_asset_ids = normalize_batch_asset_ids(asset_ids)
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = connect(database_path(library_root_path))
    try:
        with conn:
            require_existing_asset_ids(conn, requested_asset_ids)
            removed_source_records = 0
            removed_jobs = 0
            removed_renditions = 0
            removed_embeddings = 0
            for asset_id in requested_asset_ids:
                removed_source_records += int(
                    conn.execute(
                        "SELECT COUNT(*) FROM source_record WHERE asset_id = ?", (asset_id,)
                    ).fetchone()[0]
                )
                jobs, renditions, embeddings = delete_asset_rows(conn, library_root_path, asset_id)
                removed_jobs += jobs
                removed_renditions += renditions
                removed_embeddings += embeddings
        return BatchAssetActionResult(
            library_root=str(library_root_path),
            action="delete",
            requested_asset_ids=requested_asset_ids,
            affected_asset_ids=requested_asset_ids,
            skipped_running_asset_ids=[],
            removed_source_records=removed_source_records,
            removed_jobs=removed_jobs,
            removed_renditions=removed_renditions,
            removed_embeddings=removed_embeddings,
            reindex_jobs_created=0,
        )
    finally:
        conn.close()


def rebuild_active_indexes(
    library_root: Path | str, asset_ids: list[str]
) -> BatchAssetActionResult:
    requested_asset_ids = normalize_batch_asset_ids(asset_ids)
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = connect(database_path(library_root_path))
    try:
        with conn:
            require_existing_asset_ids(conn, requested_asset_ids)
            active_recipe_id = get_active_recipe_id(conn)
            affected_asset_ids: list[str] = []
            skipped_running_asset_ids: list[str] = []
            removed_embeddings = 0
            reindex_jobs_created = 0
            for asset_id in requested_asset_ids:
                running = conn.execute(
                    "SELECT 1 FROM job WHERE asset_id = ? AND recipe_id = ? AND type = ? AND status = 'running' LIMIT 1",
                    (asset_id, active_recipe_id, job_queue.JobType.EMBED_ASSET.value),
                ).fetchone()
                if running is not None:
                    skipped_running_asset_ids.append(asset_id)
                    continue
                removed_embeddings += conn.execute(
                    "DELETE FROM embedding_item WHERE asset_id = ? AND recipe_id = ? AND kind = 'image'",
                    (asset_id, active_recipe_id),
                ).rowcount
                conn.execute(
                    "DELETE FROM job WHERE asset_id = ? AND recipe_id = ? AND type = ? AND status IN ('pending', 'failed')",
                    (asset_id, active_recipe_id, job_queue.JobType.EMBED_ASSET.value),
                )
                asset_row = conn.execute(
                    "SELECT library_path, media_type FROM asset WHERE id = ?", (asset_id,)
                ).fetchone()
                reindex_jobs_created += job_queue.enqueue_embedding(
                    conn,
                    asset_id=asset_id,
                    recipe_id=active_recipe_id,
                    media_type=str(asset_row["media_type"]),
                    now=utc_now(),
                )
                affected_asset_ids.append(asset_id)
        return BatchAssetActionResult(
            library_root=str(library_root_path),
            action="rebuild-active-index",
            requested_asset_ids=requested_asset_ids,
            affected_asset_ids=affected_asset_ids,
            skipped_running_asset_ids=skipped_running_asset_ids,
            removed_source_records=0,
            removed_jobs=0,
            removed_renditions=0,
            removed_embeddings=removed_embeddings,
            reindex_jobs_created=reindex_jobs_created,
        )
    finally:
        conn.close()


def retry_failed_jobs(library_root: Path | str) -> RetryJobsResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = connect(database_path(library_root_path))
    try:
        with conn:
            retried_jobs, failed_jobs_remaining = job_queue.retry_failed_jobs(conn)
        return RetryJobsResult(
            library_root=str(library_root_path),
            retried_jobs=retried_jobs,
            failed_jobs_remaining=failed_jobs_remaining,
        )
    finally:
        conn.close()


def delete_pending_jobs(
    library_root: Path | str, job_ids: list[str]
) -> DeletePendingJobsResult:
    """Delete only queue records that have not been claimed by a worker."""
    unique_job_ids = list(dict.fromkeys(str(job_id) for job_id in job_ids if str(job_id)))
    if not unique_job_ids:
        raise ValueError("At least one pending job id is required")

    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = connect(database_path(library_root_path))
    try:
        placeholders = ", ".join("?" for _ in unique_job_ids)
        with conn:
            pending_rows = conn.execute(
                f"SELECT id FROM job WHERE status = 'pending' AND id IN ({placeholders})",
                unique_job_ids,
            ).fetchall()
            deleted_job_ids = [str(row["id"]) for row in pending_rows]
            if deleted_job_ids:
                deleted_placeholders = ", ".join("?" for _ in deleted_job_ids)
                conn.execute(
                    f"DELETE FROM job WHERE status = 'pending' AND id IN ({deleted_placeholders})",
                    deleted_job_ids,
                )
        deleted_set = set(deleted_job_ids)
        return DeletePendingJobsResult(
            requested_job_ids=unique_job_ids,
            deleted_job_ids=deleted_job_ids,
            skipped_job_ids=[job_id for job_id in unique_job_ids if job_id not in deleted_set],
        )
    finally:
        conn.close()
