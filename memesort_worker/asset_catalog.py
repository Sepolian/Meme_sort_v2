"""Asset Catalog: owns Asset lifecycle, Source Records, and write transactions.

This module is the single owner of Asset import, deletion, source record
merging, and batch operations.  It exposes a narrow interface for callers
that need to create or mutate assets; read-only projections remain in
LibraryStore.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import asset_preprocessing
from . import job_queue
from . import ocr_artifacts
from .import_contracts import (
    ImportBatchError,
    ImportBatchErrorCode,
    ImportBatchPreflightError,
    ImportBatchResult,
    ImportFailure,
    ImportFailureCode,
    ImportFailureStage,
)
from .recipe_provider import RuntimeRecipeProvider, default_provider


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

MAX_IMPORT_SOURCES = 256
MAX_IMPORT_PATH_UTF8_BYTES = 32 * 1024
MAX_IMPORT_DISCOVERED_FILES = 100_000
MAX_IMPORT_SOURCE_BYTES = 30 * 1024 * 1024
MAX_IMPORT_FRAME_PIXELS = 64_000_000
MAX_IMPORT_GIF_FRAMES = 1_000


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
class _FileImportSummary:
    discovered_files: int
    supported_files: int
    unsupported_files: int
    new_assets: int
    duplicate_assets: int
    source_records_added: int
    source_records_refreshed: int
    jobs_created: int
    failed_files: int
    failure_details: list[ImportFailure]
    active_recipe_id: str


@dataclass
class _DirectoryScanSummary:
    candidates: list[Path]
    reparse_points_skipped: int
    failure_details: list[ImportFailure]
    limit_exceeded: bool


@dataclass(frozen=True)
class _ValidatedImportSource:
    normalized_path: Path
    canonical_path: Path
    is_directory: bool


@dataclass
class _DirectoryScanGuard:
    handle: int | None
    is_reparse_point: bool
    is_directory: bool

    def close(self) -> None:
        if self.handle is None:
            return
        _close_windows_handle(self.handle)
        self.handle = None


class _CandidateImportError(Exception):
    """Expected per-candidate Import Failure that does not abort its batch."""

    def __init__(
        self,
        *,
        stage: ImportFailureStage,
        code: ImportFailureCode,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.stage = stage
        self.code = code
        self.detail = detail


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


def _is_reparse_point(metadata: object) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(getattr(metadata, "st_mode")) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_attribute
    )


def _candidate_path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _same_file_identity(first: object, second: object) -> bool:
    return (
        getattr(first, "st_dev"),
        getattr(first, "st_ino"),
    ) == (
        getattr(second, "st_dev"),
        getattr(second, "st_ino"),
    )


def _close_windows_handle(handle: int) -> None:
    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(handle))


def _open_directory_scan_guard(path: Path) -> _DirectoryScanGuard:
    """Open and lock a Windows directory without traversing a reparse point."""
    if os.name != "nt":
        return _DirectoryScanGuard(
            handle=None,
            is_reparse_point=False,
            is_directory=True,
        )

    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    file_list_directory = 0x0001
    file_share_read = 0x0001
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x0010
    file_attribute_reparse_point = 0x0400

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_file_information.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        file_list_directory,
        file_share_read,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise OSError(
            ctypes.get_last_error(),
            "The directory could not be opened for safe scanning.",
            str(path),
        )

    guard = _DirectoryScanGuard(
        handle=int(handle),
        is_reparse_point=False,
        is_directory=True,
    )
    try:
        information = ByHandleFileInformation()
        if not get_file_information(handle, ctypes.byref(information)):
            raise OSError(
                ctypes.get_last_error(),
                "The opened directory could not be inspected.",
                str(path),
            )
        attributes = int(information.file_attributes)
        guard.is_reparse_point = bool(attributes & file_attribute_reparse_point)
        guard.is_directory = bool(attributes & file_attribute_directory)
        if guard.is_reparse_point or not guard.is_directory:
            guard.close()
        return guard
    except Exception:
        guard.close()
        raise


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


def ensure_manifest_recipe(
    conn: sqlite3.Connection,
    provider: RuntimeRecipeProvider | None = None,
) -> str:
    provider = provider or default_provider()
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


def activate_manifest_recipe(
    conn: sqlite3.Connection,
    provider: RuntimeRecipeProvider | None = None,
) -> tuple[str, bool, int]:
    provider = provider or default_provider()
    recipe_id = ensure_manifest_recipe(conn, provider)
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


def _checked_processing_metadata(file_path: Path) -> os.stat_result:
    """Recheck a supported candidate immediately before it is processed."""
    try:
        metadata = os.lstat(file_path)
    except FileNotFoundError as error:
        raise _CandidateImportError(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_MISSING,
            detail="The source file is no longer available.",
        ) from error
    except OSError as error:
        raise _CandidateImportError(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_READ_FAILED,
            detail="The source file could not be inspected.",
        ) from error

    if _is_reparse_point(metadata):
        raise _CandidateImportError(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_REPARSE_POINT,
            detail="The source file became an unsafe reparse point.",
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise _CandidateImportError(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_NOT_REGULAR_FILE,
            detail="The source is no longer a regular file.",
        )
    if metadata.st_size > MAX_IMPORT_SOURCE_BYTES:
        raise _CandidateImportError(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_TOO_LARGE,
            detail="The source file exceeds the 30 MiB import limit.",
        )
    return metadata


def _source_changed_since(
    file_path: Path,
    initial_metadata: os.stat_result,
) -> bool:
    current_metadata = _checked_processing_metadata(file_path)
    return any(
        getattr(current_metadata, attribute, None)
        != getattr(initial_metadata, attribute, None)
        for attribute in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    )


def _strict_image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    try:
        return asset_preprocessing.validate_import_image_bytes(
            image_bytes,
            max_frame_pixels=MAX_IMPORT_FRAME_PIXELS,
            max_gif_frames=MAX_IMPORT_GIF_FRAMES,
        )
    except asset_preprocessing.ImportImageValidationError as error:
        raise _CandidateImportError(
            stage=ImportFailureStage.VALIDATION,
            code=ImportFailureCode(error.code),
            detail="The image could not pass strict import validation.",
        ) from error


def _validate_temporary_library_copy(file_path: Path) -> tuple[int, int]:
    try:
        if file_path.stat().st_size > MAX_IMPORT_SOURCE_BYTES:
            raise _CandidateImportError(
                stage=ImportFailureStage.COPY,
                code=ImportFailureCode.LIBRARY_COPY_TOO_LARGE,
                detail="The temporary Library Copy exceeds the 30 MiB import limit.",
            )
        image_bytes = file_path.read_bytes()
    except _CandidateImportError:
        raise
    except OSError as error:
        raise _CandidateImportError(
            stage=ImportFailureStage.COPY,
            code=ImportFailureCode.LIBRARY_COPY_FAILED,
            detail="The temporary Library Copy could not be read.",
        ) from error
    return _strict_image_dimensions(image_bytes)


def _validate_source_image(file_path: Path) -> None:
    try:
        image_bytes = file_path.read_bytes()
    except OSError as error:
        raise _CandidateImportError(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_READ_FAILED,
            detail="The source file could not be read.",
        ) from error
    _strict_image_dimensions(image_bytes)


def _delete_unreferenced_library_copy(file_path: Path) -> None:
    """Remove an owned temporary or unrecorded final Library Copy.

    A transient Windows lock must not abort unrelated candidates. Retrying
    before reporting the cleanup failure covers handles that are closing at a
    copy or database boundary.
    """
    for _ in range(3):
        try:
            file_path.unlink(missing_ok=True)
            return
        except OSError:
            continue
    raise _CandidateImportError(
        stage=ImportFailureStage.COPY,
        code=ImportFailureCode.LIBRARY_COPY_FAILED,
        detail="An unreferenced Library Copy could not be removed.",
    )


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


def initialize_library(
    root: Path | str,
    provider: RuntimeRecipeProvider | None = None,
) -> LibraryInitResult:
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
            recipe_id, _, _ = activate_manifest_recipe(conn, provider)
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


def _import_file_candidates(
    library_root_path: Path,
    file_paths: Sequence[Path],
    wait_for_permission: Callable[[], None] | None = None,
) -> _FileImportSummary:
    """Import pre-discovered candidates through the shared catalog pipeline."""
    supported_files = 0
    unsupported_files = 0
    new_assets = 0
    duplicate_assets = 0
    source_records_added = 0
    source_records_refreshed = 0
    jobs_created = 0
    failed_files = 0
    failure_details: list[ImportFailure] = []

    conn = connect(database_path(library_root_path))
    try:
        active_recipe_id = get_active_recipe_id(conn)
        ocr_recipe_id = ocr_artifacts.ensure_default_ocr_recipe(conn)
        for file_path in file_paths:
            if wait_for_permission is not None:
                wait_for_permission()

            media_type = SUPPORTED_EXTENSIONS.get(file_path.suffix.lower())
            if media_type is None:
                unsupported_files += 1
                continue

            supported_files += 1
            try:
                initial_metadata = _checked_processing_metadata(file_path)
                try:
                    content_hash = compute_sha256(file_path)
                except OSError as error:
                    raise _CandidateImportError(
                        stage=ImportFailureStage.READ,
                        code=ImportFailureCode.SOURCE_READ_FAILED,
                        detail="The source file could not be read.",
                    ) from error
                if _source_changed_since(file_path, initial_metadata):
                    raise _CandidateImportError(
                        stage=ImportFailureStage.READ,
                        code=ImportFailureCode.SOURCE_READ_FAILED,
                        detail="The source file changed while it was being read.",
                    )
                _validate_source_image(file_path)
                if _source_changed_since(file_path, initial_metadata):
                    raise _CandidateImportError(
                        stage=ImportFailureStage.READ,
                        code=ImportFailureCode.SOURCE_READ_FAILED,
                        detail="The source file changed while it was being validated.",
                    )

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
                    try:
                        with conn:
                            source_change = upsert_source_record(
                                conn=conn,
                                asset_id=str(existing_asset["id"]),
                                source_path=str(file_path),
                                now=now,
                            )
                    except sqlite3.Error as error:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.DATABASE,
                            code=ImportFailureCode.DATABASE_WRITE_FAILED,
                            detail="The duplicate Asset could not be cataloged.",
                        ) from error
                    duplicate_assets += 1
                    if source_change == "added":
                        source_records_added += 1
                    else:
                        source_records_refreshed += 1
                    continue

                asset_id = str(uuid.uuid4())
                destination_relative = (
                    Path("originals") / f"{asset_id}{file_path.suffix.lower()}"
                )
                destination_absolute = library_root_path / destination_relative
                temporary_absolute = library_root_path / "originals" / f".{asset_id}.tmp"
                try:
                    try:
                        shutil.copy2(file_path, temporary_absolute)
                    except OSError as error:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.COPY,
                            code=ImportFailureCode.LIBRARY_COPY_FAILED,
                            detail="A temporary Library Copy could not be created.",
                        ) from error

                    try:
                        temporary_size = temporary_absolute.stat().st_size
                    except OSError as error:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.COPY,
                            code=ImportFailureCode.LIBRARY_COPY_FAILED,
                            detail="The temporary Library Copy could not be inspected.",
                        ) from error
                    if temporary_size > MAX_IMPORT_SOURCE_BYTES:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.COPY,
                            code=ImportFailureCode.LIBRARY_COPY_TOO_LARGE,
                            detail="The temporary Library Copy exceeds the 30 MiB import limit.",
                        )

                    try:
                        temporary_hash = compute_sha256(temporary_absolute)
                    except OSError as error:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.COPY,
                            code=ImportFailureCode.LIBRARY_COPY_FAILED,
                            detail="The temporary Library Copy could not be read.",
                        ) from error
                    if temporary_hash != content_hash:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.COPY,
                            code=ImportFailureCode.LIBRARY_COPY_HASH_MISMATCH,
                            detail="The source changed while its Library Copy was created.",
                        )

                    width, height = _validate_temporary_library_copy(temporary_absolute)
                    if _source_changed_since(file_path, initial_metadata):
                        raise _CandidateImportError(
                            stage=ImportFailureStage.READ,
                            code=ImportFailureCode.SOURCE_READ_FAILED,
                            detail="The source file changed while it was being copied.",
                        )
                    try:
                        os.replace(temporary_absolute, destination_absolute)
                    except OSError as error:
                        raise _CandidateImportError(
                            stage=ImportFailureStage.COPY,
                            code=ImportFailureCode.LIBRARY_COPY_FAILED,
                            detail="The verified Library Copy could not be finalized.",
                        ) from error
                finally:
                    _delete_unreferenced_library_copy(temporary_absolute)

                try:
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
                                destination_absolute.stat().st_size,
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
                        created_jobs = job_queue.enqueue_thumbnail(
                            conn=conn,
                            asset_id=asset_id,
                            now=now,
                        )
                        created_jobs += job_queue.enqueue_embedding(
                            conn=conn,
                            asset_id=asset_id,
                            recipe_id=active_recipe_id,
                            media_type=media_type,
                            now=now,
                        )
                        created_jobs += ocr_artifacts.enqueue_ocr_asset_job(
                            conn,
                            asset_id=asset_id,
                            media_type=media_type,
                            now=now,
                            ocr_recipe_id=ocr_recipe_id,
                        )
                except (OSError, sqlite3.Error) as error:
                    _delete_unreferenced_library_copy(destination_absolute)
                    raise _CandidateImportError(
                        stage=ImportFailureStage.DATABASE,
                        code=ImportFailureCode.DATABASE_WRITE_FAILED,
                        detail="The verified Library Copy could not be cataloged.",
                    ) from error

                new_assets += 1
                jobs_created += created_jobs
                if source_change == "added":
                    source_records_added += 1
                else:
                    source_records_refreshed += 1
            except _CandidateImportError as error:
                failed_files += 1
                failure_details.append(
                    ImportFailure(
                        stage=error.stage,
                        code=error.code,
                        source_name=file_path.name,
                        detail=error.detail,
                    )
                )
    finally:
        conn.close()

    return _FileImportSummary(
        discovered_files=len(file_paths),
        supported_files=supported_files,
        unsupported_files=unsupported_files,
        new_assets=new_assets,
        duplicate_assets=duplicate_assets,
        source_records_added=source_records_added,
        source_records_refreshed=source_records_refreshed,
        jobs_created=jobs_created,
        failed_files=failed_files,
        failure_details=failure_details,
        active_recipe_id=active_recipe_id,
    )


def _append_unique_candidate(
    candidate: Path,
    candidates: list[Path],
    candidate_keys: set[str],
    failure_details: list[ImportFailure],
) -> bool:
    candidate_key = _candidate_path_key(candidate)
    if candidate_key in candidate_keys:
        return False
    if len(candidate_keys) >= MAX_IMPORT_DISCOVERED_FILES:
        failure_details.append(
            ImportFailure(
                stage=ImportFailureStage.SCAN,
                code=ImportFailureCode.FILE_LIMIT_EXCEEDED,
                source_name=str(candidate),
                detail=(
                    "Scanning stopped after the unique-file discovery limit was "
                    "reached."
                ),
            )
        )
        return True

    candidate_keys.add(candidate_key)
    candidates.append(candidate)
    return False


def _scan_directory_candidates(
    source_directory: Path,
    candidate_keys: set[str],
    wait_for_permission: Callable[[], None] | None = None,
) -> _DirectoryScanSummary:
    """Discover regular files without following descendant reparse points."""
    candidates: list[Path] = []
    reparse_points_skipped = 0
    failure_details: list[ImportFailure] = []
    limit_exceeded = False
    pending_scan_work: list[Path | _DirectoryScanGuard] = [source_directory]
    directory_guards: list[_DirectoryScanGuard] = []

    try:
        if source_directory.parent != source_directory:
            try:
                source_parent_guard = _open_directory_scan_guard(
                    source_directory.parent
                )
            except OSError:
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(source_directory),
                        detail="The source directory parent could not be locked.",
                    )
                )
                return _DirectoryScanSummary(
                    candidates=candidates,
                    reparse_points_skipped=reparse_points_skipped,
                    failure_details=failure_details,
                    limit_exceeded=limit_exceeded,
                )
            if source_parent_guard.is_reparse_point:
                reparse_points_skipped += 1
                return _DirectoryScanSummary(
                    candidates=candidates,
                    reparse_points_skipped=reparse_points_skipped,
                    failure_details=failure_details,
                    limit_exceeded=limit_exceeded,
                )
            if not source_parent_guard.is_directory:
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(source_directory),
                        detail="The source directory parent is not a directory.",
                    )
                )
                return _DirectoryScanSummary(
                    candidates=candidates,
                    reparse_points_skipped=reparse_points_skipped,
                    failure_details=failure_details,
                    limit_exceeded=limit_exceeded,
                )
            directory_guards.append(source_parent_guard)

        while pending_scan_work:
            scan_work = pending_scan_work.pop()
            if isinstance(scan_work, _DirectoryScanGuard):
                scan_work.close()
                continue
            current_directory = scan_work
            try:
                current_metadata = os.lstat(current_directory)
            except OSError:
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(current_directory),
                        detail="The directory could not be inspected before scanning.",
                    )
                )
                continue
            if _is_reparse_point(current_metadata):
                reparse_points_skipped += 1
                continue
            if not stat.S_ISDIR(current_metadata.st_mode):
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(current_directory),
                        detail="The directory changed before it could be scanned.",
                    )
                )
                continue

            try:
                directory_guard = _open_directory_scan_guard(current_directory)
            except OSError:
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(current_directory),
                        detail="The directory could not be locked for safe scanning.",
                    )
                )
                continue
            if directory_guard.is_reparse_point:
                reparse_points_skipped += 1
                continue
            if not directory_guard.is_directory:
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(current_directory),
                        detail="The directory changed before it could be locked.",
                    )
                )
                continue
            directory_guards.append(directory_guard)

            try:
                with os.scandir(current_directory) as entries:
                    opened_directory_metadata = os.lstat(current_directory)
                    if _is_reparse_point(opened_directory_metadata):
                        reparse_points_skipped += 1
                        directory_guard.close()
                        continue
                    if not _same_file_identity(
                        current_metadata,
                        opened_directory_metadata,
                    ):
                        failure_details.append(
                            ImportFailure(
                                stage=ImportFailureStage.SCAN,
                                code=ImportFailureCode.SCAN_FAILED,
                                source_name=str(current_directory),
                                detail="The directory changed while it was being opened.",
                            )
                        )
                        directory_guard.close()
                        continue
                    ordered_entries = sorted(
                        entries,
                        key=lambda entry: (os.path.normcase(entry.name), entry.name),
                    )
            except OSError:
                failure_details.append(
                    ImportFailure(
                        stage=ImportFailureStage.SCAN,
                        code=ImportFailureCode.SCAN_FAILED,
                        source_name=str(current_directory),
                        detail="The directory could not be scanned.",
                    )
                )
                directory_guard.close()
                continue

            child_directories: list[Path] = []
            for entry in ordered_entries:
                if wait_for_permission is not None:
                    wait_for_permission()

                try:
                    metadata = os.lstat(entry.path)
                except OSError:
                    failure_details.append(
                        ImportFailure(
                            stage=ImportFailureStage.SCAN,
                            code=ImportFailureCode.SCAN_FAILED,
                            source_name=entry.name,
                            detail="The directory entry could not be inspected.",
                        )
                    )
                    continue
                if _is_reparse_point(metadata):
                    reparse_points_skipped += 1
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    child_directories.append(Path(entry.path))
                elif stat.S_ISREG(metadata.st_mode):
                    limit_exceeded = _append_unique_candidate(
                        Path(entry.path),
                        candidates,
                        candidate_keys,
                        failure_details,
                    )
                    if limit_exceeded:
                        break

            if limit_exceeded:
                directory_guard.close()
                break
            if child_directories:
                pending_scan_work.append(directory_guard)
                pending_scan_work.extend(reversed(child_directories))
            else:
                directory_guard.close()

        return _DirectoryScanSummary(
            candidates=candidates,
            reparse_points_skipped=reparse_points_skipped,
            failure_details=failure_details,
            limit_exceeded=limit_exceeded,
        )
    finally:
        for directory_guard in reversed(directory_guards):
            directory_guard.close()


def import_folder(
    library_root: Path | str,
    source_folder: Path | str,
    wait_for_permission: Callable[[], None] | None = None,
    provider: RuntimeRecipeProvider | None = None,
) -> ImportFolderResult:
    """Adapt the legacy single-folder API to the multi-source import seam."""
    source_root = resolve_path(source_folder)
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError(
            f"Source folder does not exist or is not a directory: {source_root}"
        )

    batch_result = import_sources(
        library_root,
        [source_folder],
        wait_for_permission=wait_for_permission,
        provider=provider,
    )
    assert batch_result.active_recipe_id is not None
    return ImportFolderResult(
        library_root=batch_result.library_root,
        source_folder=str(source_root),
        discovered_files=batch_result.discovered_files,
        supported_files=batch_result.supported_files,
        unsupported_files=batch_result.unsupported_files,
        new_assets=batch_result.new_assets,
        duplicate_assets=batch_result.duplicate_assets,
        source_records_added=batch_result.source_records_added,
        source_records_refreshed=batch_result.source_records_refreshed,
        jobs_created=batch_result.jobs_created,
        active_recipe_id=batch_result.active_recipe_id,
    )


def import_sources(
    library_root: Path | str,
    sources: Sequence[Path | str],
    wait_for_permission: Callable[[], None] | None = None,
    provider: RuntimeRecipeProvider | None = None,
) -> ImportBatchResult:
    """Import files and directories as one synchronous Import Batch."""
    if not isinstance(sources, Sequence) or isinstance(
        sources,
        (str, bytes, bytearray),
    ):
        raise ImportBatchPreflightError(
            code=ImportBatchErrorCode.INVALID_SOURCE,
            detail="Import Sources must be provided as a sequence of paths.",
        )
    if not sources or len(sources) > MAX_IMPORT_SOURCES:
        raise ImportBatchPreflightError(
            code=ImportBatchErrorCode.INVALID_SOURCE,
            detail=f"An Import Batch requires 1 to {MAX_IMPORT_SOURCES} sources.",
        )
    if any(not isinstance(source, (str, Path)) for source in sources):
        raise ImportBatchPreflightError(
            code=ImportBatchErrorCode.INVALID_SOURCE,
            detail="Every Import Source must be a string or Path.",
        )
    for source in sources:
        source_text = str(source)
        try:
            encoded_source = source_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            encoded_source = b""
        if (
            not encoded_source
            or len(encoded_source) > MAX_IMPORT_PATH_UTF8_BYTES
            or any(character in source_text for character in ("\x00", "\r", "\n"))
        ):
            raise ImportBatchPreflightError(
                code=ImportBatchErrorCode.INVALID_SOURCE,
                detail="Every Import Source path must be transport-safe UTF-8.",
            )
    library_root_path = resolve_path(library_root)
    sources_by_canonical_key: dict[str, _ValidatedImportSource] = {}
    for source in sources:
        normalized_source = Path(
            os.path.abspath(Path(source).expanduser())
        )
        try:
            source_metadata = os.lstat(normalized_source)
        except OSError as error:
            raise ImportBatchPreflightError(
                code=ImportBatchErrorCode.INVALID_SOURCE,
                detail="An Import Source does not exist or cannot be inspected.",
            ) from error
        if _is_reparse_point(source_metadata):
            raise ImportBatchPreflightError(
                code=ImportBatchErrorCode.INVALID_SOURCE,
                detail="Top-level reparse-point Import Sources are not allowed.",
            )
        if not (
            stat.S_ISREG(source_metadata.st_mode)
            or stat.S_ISDIR(source_metadata.st_mode)
        ):
            raise ImportBatchPreflightError(
                code=ImportBatchErrorCode.INVALID_SOURCE,
                detail="Every Import Source must be a regular file or directory.",
            )
        try:
            canonical_source = normalized_source.resolve(strict=True)
        except OSError as error:
            raise ImportBatchPreflightError(
                code=ImportBatchErrorCode.INVALID_SOURCE,
                detail="An Import Source could not be resolved.",
            ) from error
        if (
            canonical_source == library_root_path
            or canonical_source.is_relative_to(library_root_path)
            or library_root_path.is_relative_to(canonical_source)
        ):
            raise ImportBatchPreflightError(
                code=ImportBatchErrorCode.INVALID_SOURCE,
                detail="Import Sources must be outside the Library Root.",
            )
        canonical_key = os.path.normcase(str(canonical_source))
        sources_by_canonical_key.setdefault(
            canonical_key,
            _ValidatedImportSource(
                normalized_path=normalized_source,
                canonical_path=canonical_source,
                is_directory=stat.S_ISDIR(source_metadata.st_mode),
            ),
        )

    effective_sources = [
        source
        for canonical_key, source in sources_by_canonical_key.items()
        if not any(
            directory_key != canonical_key
            and source.canonical_path.is_relative_to(directory.canonical_path)
            for directory_key, directory in sources_by_canonical_key.items()
            if directory.is_directory
        )
    ]
    file_paths: list[Path] = []
    candidate_keys: set[str] = set()
    reparse_points_skipped = 0
    scan_failure_details: list[ImportFailure] = []
    limit_exceeded = False
    for source in effective_sources:
        if source.is_directory:
            directory_scan = _scan_directory_candidates(
                source.normalized_path,
                candidate_keys,
                wait_for_permission=wait_for_permission,
            )
            file_paths.extend(directory_scan.candidates)
            reparse_points_skipped += directory_scan.reparse_points_skipped
            scan_failure_details.extend(directory_scan.failure_details)
            limit_exceeded = directory_scan.limit_exceeded
        else:
            limit_exceeded = _append_unique_candidate(
                source.normalized_path,
                file_paths,
                candidate_keys,
                scan_failure_details,
            )

        if limit_exceeded:
            break

    if limit_exceeded:
        supported_files = sum(
            source_path.suffix.lower() in SUPPORTED_EXTENSIONS
            for source_path in file_paths
        )
        partial_result = ImportBatchResult(
            library_root=str(library_root_path),
            selected_sources=len(sources),
            effective_sources=len(effective_sources),
            discovered_files=len(file_paths),
            supported_files=supported_files,
            unsupported_files=len(file_paths) - supported_files,
            reparse_points_skipped=reparse_points_skipped,
            scan_failures=len(scan_failure_details),
            processed_files=0,
            succeeded_files=0,
            failed_files=0,
            new_assets=0,
            duplicate_assets=0,
            source_records_added=0,
            source_records_refreshed=0,
            jobs_created=0,
            failure_details=tuple(scan_failure_details),
            active_recipe_id=None,
        )
        raise ImportBatchError(
            code=ImportBatchErrorCode.FILE_LIMIT_EXCEEDED,
            detail=(
                "The Import Batch reached the 100,000 unique-file discovery "
                "limit before Library writes began."
            ),
            partial_result=partial_result,
        )

    init_result = initialize_library(library_root_path, provider)
    library_root_path = Path(init_result.library_root)
    summary = _import_file_candidates(
        library_root_path,
        file_paths,
        wait_for_permission=wait_for_permission,
    )
    succeeded_files = summary.new_assets + summary.duplicate_assets
    return ImportBatchResult(
        library_root=str(library_root_path),
        selected_sources=len(sources),
        effective_sources=len(effective_sources),
        discovered_files=summary.discovered_files,
        supported_files=summary.supported_files,
        unsupported_files=summary.unsupported_files,
        reparse_points_skipped=reparse_points_skipped,
        scan_failures=len(scan_failure_details),
        processed_files=summary.supported_files,
        succeeded_files=succeeded_files,
        failed_files=summary.failed_files,
        new_assets=summary.new_assets,
        duplicate_assets=summary.duplicate_assets,
        source_records_added=summary.source_records_added,
        source_records_refreshed=summary.source_records_refreshed,
        jobs_created=summary.jobs_created,
        failure_details=tuple(scan_failure_details + summary.failure_details),
        active_recipe_id=summary.active_recipe_id,
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
