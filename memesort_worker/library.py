from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from .embedding_backend import (
    EmbeddingBackend,
    get_embedding_backend,
)
from . import ocr_artifacts
from . import job_queue
from .semantic_retrieval import scan_duplicate_vector_rows
from .runtime_manifest import load_runtime_manifest


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

_RUNTIME_MANIFEST = load_runtime_manifest()
VULKAN_PROFILE_ID = "vulkan"
MANIFEST_MODEL_KEY = "manifest"
MANIFEST_RECIPE_PRESET = "vulkan-manifest"
_VULKAN_RECIPE_PRESET = MANIFEST_RECIPE_PRESET
_VULKAN_POOLING_KEY = (
    f"{_RUNTIME_MANIFEST.embedding.pooling}-"
    f"{_RUNTIME_MANIFEST.embedding.normalization}-"
    f"{_RUNTIME_MANIFEST.embedding.storage_dtype}"
)

DEFAULT_RECIPE = {
    "family_key": _RUNTIME_MANIFEST.model.protocol,
    "model_id": _RUNTIME_MANIFEST.model.id,
    "model_revision": _RUNTIME_MANIFEST.recipe_fingerprint,
    "output_dimension": _RUNTIME_MANIFEST.model.output_dimension,
    "runtime_profile": VULKAN_PROFILE_ID,
    "preprocess_version": _RUNTIME_MANIFEST.preprocessing.version,
    "instruction_key": _RUNTIME_MANIFEST.embedding.instruction_id,
    "pooling_key": _VULKAN_POOLING_KEY,
    "normalized": 1,
    "gif_frame_count": _RUNTIME_MANIFEST.preprocessing.gif_frame_count,
}

RECIPE_PRESETS: dict[str, dict[str, object]] = {
    MANIFEST_RECIPE_PRESET: dict(DEFAULT_RECIPE),
}

INSTRUCTION_TEXT_BY_KEY = {
    _RUNTIME_MANIFEST.embedding.instruction_id: _RUNTIME_MANIFEST.embedding.instruction,
}

PREPROCESS_SPECS_BY_VERSION = {
    _RUNTIME_MANIFEST.preprocessing.version: {
        "still_max_side": _RUNTIME_MANIFEST.preprocessing.still_max_side,
        "gif_max_side": _RUNTIME_MANIFEST.preprocessing.gif_max_side,
    },
}

DEFAULT_GIF_FRAME_COUNT = _RUNTIME_MANIFEST.preprocessing.gif_frame_count
DEFAULT_OCR_RECIPE = ocr_artifacts.DEFAULT_OCR_RECIPE


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
class DeletePendingJobsResult:
    requested_job_ids: list[str]
    deleted_job_ids: list[str]
    skipped_job_ids: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SwitchRecipeResult:
    library_root: str
    active_recipe_id: str
    active_recipe_label: str
    assets_seen: int
    reindex_jobs_created: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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
class RuntimeProfileSpec:
    profile_id: str
    label: str
    recipe_preset: str
    model_id: str
    device: str
    torch_dtype: str
    num_threads: int | None
    num_interop_threads: int | None
    still_max_side: int
    gif_max_side: int
    gif_frame_count: int
    notes: str
    backend_name: str = "llama.cpp"
    supported_model_keys: tuple[str, ...] = (MANIFEST_MODEL_KEY,)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ModelVariantSpec:
    model_key: str
    label: str
    model_id: str
    output_dimension: int
    notes: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RuntimeSettings:
    selected_profile: str
    selected_model_key: str
    model_name_or_path: str | None
    selected_recipe_preset: str
    gif_frame_count: int
    backend_name: str
    library_root: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RuntimeHealthResult:
    profile_id: str
    backend_name: str
    model_name_or_path: str | None
    selected_model_key: str | None
    selected_model_label: str | None
    device: str
    torch_dtype: str
    torch_available: bool
    cuda_available: bool
    gpu_name: str | None
    model_source_origin: str | None
    model_downloaded: bool
    text_smoke_vector_dim: int | None
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
    runtime_profile_selected: bool
    embedding_model_selected: bool
    model_path_configured: bool
    runtime_backend_selected: bool
    health_check_has_run: bool
    health_check_ok: bool
    health_check_summary: str
    import_source_hint: str | None
    assets_present: bool
    indexed_assets_present: bool
    pending_assets_present: bool
    active_recipe_label: str
    suggested_model_path: str | None
    runtime_readiness: dict[str, object]
    checklist: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


RUNTIME_PROFILES: dict[str, RuntimeProfileSpec] = {
    VULKAN_PROFILE_ID: RuntimeProfileSpec(
        profile_id=VULKAN_PROFILE_ID,
        label="Vulkan0 (llama.cpp)",
        recipe_preset=MANIFEST_RECIPE_PRESET,
        model_id=_RUNTIME_MANIFEST.model.id,
        device=_RUNTIME_MANIFEST.platform.device,
        torch_dtype="gguf-q4_k_m",
        num_threads=None,
        num_interop_threads=None,
        still_max_side=_RUNTIME_MANIFEST.preprocessing.still_max_side,
        gif_max_side=_RUNTIME_MANIFEST.preprocessing.gif_max_side,
        gif_frame_count=_RUNTIME_MANIFEST.preprocessing.gif_frame_count,
        notes="Pinned Vulkan0 backend for supported AMD, Intel, and NVIDIA GPUs.",
        backend_name="llama.cpp",
        supported_model_keys=(MANIFEST_MODEL_KEY,),
    ),
}

MODEL_VARIANTS: dict[str, ModelVariantSpec] = {
    MANIFEST_MODEL_KEY: ModelVariantSpec(
        model_key=MANIFEST_MODEL_KEY,
        label=_RUNTIME_MANIFEST.model.base_model_id.split("/")[-1],
        model_id=_RUNTIME_MANIFEST.model.id,
        output_dimension=_RUNTIME_MANIFEST.model.output_dimension,
        notes="Developer-controlled by runtime-manifest.json.",
    ),
}

MODEL_PRESET_BY_PROFILE: dict[str, dict[str, str]] = {
    MANIFEST_MODEL_KEY: {
        VULKAN_PROFILE_ID: MANIFEST_RECIPE_PRESET,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def discover_local_gguf_model_path(model_key: str) -> str | None:
    get_model_variant(model_key)
    if not _RUNTIME_MANIFEST.main_model_path.is_file():
        return None
    if not _RUNTIME_MANIFEST.projector_path.is_file():
        return None
    return str(_RUNTIME_MANIFEST.model_install_dir)


def resolve_effective_model_source_for_backend(
    backend_name: str,
    selected_model_key: str,
    configured_model_name_or_path: str | None,
    allow_download: bool = False,
) -> str | None:
    del allow_download
    if backend_name != "llama.cpp":
        raise ValueError("MemeSort supports only the llama.cpp Vulkan backend")
    if selected_model_key != MANIFEST_MODEL_KEY:
        raise ValueError("Embedding model is owned by runtime-manifest.json")
    if configured_model_name_or_path is not None:
        raise ValueError("Embedding model path is owned by runtime-manifest.json")
    return str(_RUNTIME_MANIFEST.model_install_dir)


def is_runtime_ready_for_indexing(library_root: Path | str) -> tuple[bool, str]:
    settings = get_runtime_settings(library_root)
    effective_model_source = resolve_effective_model_source_for_backend(
        settings.backend_name,
        settings.selected_model_key,
        settings.model_name_or_path,
    )
    if settings.backend_name != "llama.cpp":
        return False, f"Unsupported runtime backend: {settings.backend_name}."
    if not effective_model_source:
        return False, "Model source is not ready yet."
    last_health_check = get_last_health_check(library_root)
    if last_health_check is None:
        return False, "Runtime health check has not been run yet."
    if not runtime_health_matches_settings(settings, last_health_check):
        return False, "Runtime health check is stale for the current profile, model, or backend."
    if not last_health_check.smoke_test_ok:
        return False, last_health_check.error or "Runtime health check failed."
    return True, "Runtime is ready for indexing."


def runtime_health_matches_settings(
    settings: RuntimeSettings,
    health_check: RuntimeHealthResult,
) -> bool:
    return (
        health_check.profile_id == settings.selected_profile
        and health_check.backend_name == settings.backend_name
        and health_check.selected_model_key == settings.selected_model_key
    )


def _database_path(library_root: Path) -> Path:
    return library_root / DATABASE_NAME


def _connect(database_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
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


def _recipe_label(
    model_id: str,
    output_dimension: int,
    runtime_profile: str,
    gif_frame_count: int = DEFAULT_GIF_FRAME_COUNT,
) -> str:
    model_name = model_id.split("/")[-1]
    base = f"{model_name} / {output_dimension}d / {runtime_profile}"
    if gif_frame_count == DEFAULT_GIF_FRAME_COUNT:
        return base
    return f"{base} / gif-f{gif_frame_count}"


def _ensure_recipe(conn: sqlite3.Connection, recipe_spec: dict[str, object]) -> str:
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
            _utc_now(),
        ),
    )
    return recipe_id


def _ensure_default_recipe(conn: sqlite3.Connection) -> str:
    return _ensure_recipe(conn, DEFAULT_RECIPE)


def _activate_manifest_recipe(conn: sqlite3.Connection) -> tuple[str, bool, int]:
    recipe_id = _ensure_default_recipe(conn)
    active_recipe_id = _get_active_recipe_id(conn)
    state = _get_worker_state_json(conn, "semantic_recipe_activation")
    already_active = (
        active_recipe_id == recipe_id
        and state is not None
        and state.get("recipe_fingerprint") == _RUNTIME_MANIFEST.recipe_fingerprint
        and state.get("recipe_id") == recipe_id
    )
    if already_active:
        return recipe_id, False, 0

    first_activation = state is None and active_recipe_id == recipe_id
    if first_activation:
        _set_worker_state_json(
            conn,
            "semantic_recipe_activation",
            {
                "recipe_fingerprint": _RUNTIME_MANIFEST.recipe_fingerprint,
                "recipe_id": recipe_id,
            },
        )
        return recipe_id, False, 0

    conn.execute("DELETE FROM embedding_item")
    conn.execute("DELETE FROM job WHERE type = 'embed_asset'")
    conn.execute("DELETE FROM embedding_recipe WHERE id != ?", (recipe_id,))
    _set_active_recipe_id(conn, recipe_id)
    conn.execute("DELETE FROM worker_state WHERE key = 'last_runtime_health_check'")

    queued = 0
    now = _utc_now()
    asset_rows = conn.execute(
        "SELECT id, media_type FROM asset WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    for asset_row in asset_rows:
        asset_id = str(asset_row["id"])
        queued += _create_job(
            conn,
            job_type="embed_asset",
            asset_id=asset_id,
            recipe_id=recipe_id,
            payload={
                "asset_id": asset_id,
                "recipe_id": recipe_id,
                "media_type": str(asset_row["media_type"]),
            },
            now=now,
        )
    _set_worker_state_json(
        conn,
        "semantic_recipe_activation",
        {
            "recipe_fingerprint": _RUNTIME_MANIFEST.recipe_fingerprint,
            "recipe_id": recipe_id,
        },
    )
    return recipe_id, True, queued


def _ensure_ocr_recipe(conn: sqlite3.Connection, recipe_spec: dict[str, object]) -> str:
    return ocr_artifacts.ensure_ocr_recipe(conn, recipe_spec)


def _ensure_default_ocr_recipe(conn: sqlite3.Connection) -> str:
    return ocr_artifacts.ensure_default_ocr_recipe(conn)


def _ensure_missing_ocr_jobs(conn: sqlite3.Connection) -> int:
    return ocr_artifacts.ensure_missing_ocr_jobs(
        conn,
        create_job=_create_job,
        now=_utc_now(),
    )


def _recipe_spec_for_preset(
    preset_key: str,
    gif_frame_count: int | None = None,
) -> dict[str, object]:
    if preset_key not in RECIPE_PRESETS:
        raise ValueError(f"Unknown recipe preset: {preset_key}")
    recipe_spec = dict(RECIPE_PRESETS[preset_key])
    if gif_frame_count is not None:
        if gif_frame_count <= 0:
            raise ValueError("gif_frame_count must be positive")
        if (
            preset_key == _VULKAN_RECIPE_PRESET
            and gif_frame_count != _RUNTIME_MANIFEST.preprocessing.gif_frame_count
        ):
            raise ValueError(
                "The Vulkan GIF frame count is pinned by runtime-manifest.json"
            )
        recipe_spec["gif_frame_count"] = gif_frame_count
    return recipe_spec


def _validate_recipe_preset_for_profile(
    profile_id: str,
    preset_key: str,
) -> None:
    recipe_spec = _recipe_spec_for_preset(preset_key)
    recipe_profile_id = str(recipe_spec["runtime_profile"])
    if recipe_profile_id != profile_id:
        raise ValueError(
            f"Recipe preset {preset_key} requires runtime profile "
            f"{recipe_profile_id}, not {profile_id}"
        )


def _get_worker_state_json(
    conn: sqlite3.Connection,
    key: str,
) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT value_json FROM worker_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(str(row["value_json"]))


def _set_worker_state_json(
    conn: sqlite3.Connection,
    key: str,
    payload: dict[str, object],
) -> None:
    now = _utc_now()
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


def _set_active_recipe_id(conn: sqlite3.Connection, recipe_id: str) -> None:
    _set_worker_state_json(conn, "active_recipe_id", {"recipe_id": recipe_id})


def _get_active_recipe_id(conn: sqlite3.Connection) -> str:
    payload = _get_worker_state_json(conn, "active_recipe_id")
    if payload is None:
        recipe_id = _ensure_default_recipe(conn)
        _set_active_recipe_id(conn, recipe_id)
        return recipe_id

    recipe_id = str(payload["recipe_id"])
    existing = conn.execute(
        "SELECT id FROM embedding_recipe WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if existing is not None:
        return recipe_id

    fallback = _ensure_default_recipe(conn)
    _set_active_recipe_id(conn, fallback)
    return fallback


def _get_recipe_row(conn: sqlite3.Connection, recipe_id: str) -> sqlite3.Row:
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
    instruction = INSTRUCTION_TEXT_BY_KEY.get(instruction_key)
    if instruction is None:
        raise ValueError(f"Unsupported instruction key: {instruction_key}")
    return instruction


def _preprocess_image_bytes(
    image_bytes: bytes,
    preprocess_version: str,
) -> bytes:
    preprocess_spec = PREPROCESS_SPECS_BY_VERSION.get(preprocess_version)
    if preprocess_spec is None:
        raise ValueError(f"Unsupported preprocess version: {preprocess_version}")
    max_side = int(preprocess_spec["still_max_side"])

    with Image.open(io.BytesIO(image_bytes)) as image:
        oriented = ImageOps.exif_transpose(image)
        rgb = _composite_manifest_rgb(oriented)
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG")
        return buffer.getvalue()


def _image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    return int(width), int(height)


def _safe_image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int | None, int | None]:
    try:
        return _image_dimensions_from_bytes(image_bytes)
    except Exception:
        return None, None


def _extract_gif_frame_bytes(
    image_bytes: bytes,
    preprocess_version: str,
    frame_count: int = DEFAULT_GIF_FRAME_COUNT,
) -> list[tuple[int, bytes]]:
    preprocess_spec = PREPROCESS_SPECS_BY_VERSION.get(preprocess_version)
    if preprocess_spec is None:
        raise ValueError(f"Unsupported preprocess version: {preprocess_version}")
    max_side = int(preprocess_spec["gif_max_side"])
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")

    frame_payloads: list[tuple[int, bytes]] = []
    with Image.open(io.BytesIO(image_bytes)) as image:
        total_frames = max(1, int(getattr(image, "n_frames", 1)))
        if total_frames <= frame_count:
            selected_indexes = list(range(total_frames))
        else:
            selected_indexes = sorted(
                {
                    round(index * (total_frames - 1) / (frame_count - 1))
                    for index in range(frame_count)
                }
            )

        for frame_index in selected_indexes:
            image.seek(frame_index)
            rgb = _composite_manifest_rgb(image)
            rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="PNG")
            frame_payloads.append((frame_index, buffer.getvalue()))
    return frame_payloads


def _composite_manifest_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new(
            "RGB",
            rgba.size,
            _RUNTIME_MANIFEST.preprocessing.alpha_background,
        )
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert(_RUNTIME_MANIFEST.preprocessing.color_mode)


def _gif_frame_count_for_recipe(recipe_row: sqlite3.Row) -> int:
    value = recipe_row["gif_frame_count"]
    frame_count = DEFAULT_GIF_FRAME_COUNT if value is None else int(value)
    if frame_count <= 0:
        raise ValueError(f"Invalid gif_frame_count on recipe {recipe_row['id']}: {frame_count}")
    return frame_count


def list_runtime_profiles() -> list[RuntimeProfileSpec]:
    return [RUNTIME_PROFILES[key] for key in sorted(RUNTIME_PROFILES.keys())]


def list_model_variants() -> list[ModelVariantSpec]:
    return [MODEL_VARIANTS[key] for key in sorted(MODEL_VARIANTS.keys())]


def get_runtime_profile(profile_id: str) -> RuntimeProfileSpec:
    try:
        return RUNTIME_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown runtime profile: {profile_id}") from exc


def get_model_variant(model_key: str) -> ModelVariantSpec:
    try:
        return MODEL_VARIANTS[model_key]
    except KeyError as exc:
        raise ValueError(f"Unknown model variant: {model_key}") from exc


def resolve_recipe_preset(profile_id: str, model_key: str) -> str:
    profile = get_runtime_profile(profile_id)
    get_model_variant(model_key)
    if model_key not in profile.supported_model_keys:
        raise ValueError(
            f"Runtime profile {profile_id} does not support model variant {model_key}"
        )
    try:
        return MODEL_PRESET_BY_PROFILE[model_key][profile_id]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported runtime profile {profile_id} for model variant {model_key}"
        ) from exc


def get_runtime_settings(library_root: Path | str) -> RuntimeSettings:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    return RuntimeSettings(
        selected_profile=VULKAN_PROFILE_ID,
        selected_model_key=MANIFEST_MODEL_KEY,
        model_name_or_path=None,
        selected_recipe_preset=MANIFEST_RECIPE_PRESET,
        gif_frame_count=_RUNTIME_MANIFEST.preprocessing.gif_frame_count,
        backend_name="llama.cpp",
        library_root=str(library_root_path),
    )


def _save_last_health_check(
    library_root: Path | str,
    result: RuntimeHealthResult,
) -> None:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        with conn:
            _set_worker_state_json(conn, "last_runtime_health_check", result.to_dict())
    finally:
        conn.close()


def get_last_health_check(
    library_root: Path | str,
) -> RuntimeHealthResult | None:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        payload = _get_worker_state_json(conn, "last_runtime_health_check")
        if payload is None:
            return None
        return RuntimeHealthResult(
            profile_id=str(payload["profile_id"]),
            backend_name=str(payload["backend_name"]),
            model_name_or_path=(
                str(payload["model_name_or_path"])
                if payload.get("model_name_or_path")
                else None
            ),
            selected_model_key=(
                str(payload["selected_model_key"])
                if payload.get("selected_model_key")
                else None
            ),
            selected_model_label=(
                str(payload["selected_model_label"])
                if payload.get("selected_model_label")
                else None
            ),
            device=str(payload["device"]),
            torch_dtype=str(payload["torch_dtype"]),
            torch_available=bool(payload["torch_available"]),
            cuda_available=bool(payload["cuda_available"]),
            gpu_name=str(payload["gpu_name"]) if payload.get("gpu_name") else None,
            model_source_origin=(
                str(payload["model_source_origin"])
                if payload.get("model_source_origin")
                else None
            ),
            model_downloaded=bool(payload.get("model_downloaded", False)),
            text_smoke_vector_dim=(
                int(payload["text_smoke_vector_dim"])
                if payload.get("text_smoke_vector_dim") is not None
                else None
            ),
            diagnostic_steps=list(payload.get("diagnostic_steps", [])),
            smoke_test_ok=bool(payload["smoke_test_ok"]),
            error=str(payload["error"]) if payload.get("error") else None,
        )
    finally:
        conn.close()


def run_runtime_health_check(
    profile_id: str,
    model_key: str = MANIFEST_MODEL_KEY,
    model_name_or_path: str | None = None,
    library_root: Path | str | None = None,
) -> RuntimeHealthResult:
    from .runtime_service import run_runtime_health_check as _run_runtime_health_check

    return _run_runtime_health_check(
        profile_id,
        model_key=model_key,
        model_name_or_path=model_name_or_path,
        library_root=library_root,
    )


def initialize_library(root: Path | str) -> LibraryInitResult:
    library_root = _resolve(root)
    library_root.mkdir(parents=True, exist_ok=True)
    for relative_dir in LIBRARY_DIRS:
        (library_root / relative_dir).mkdir(parents=True, exist_ok=True)

    database_path = _database_path(library_root)
    created_database = not database_path.exists()

    conn = _connect(database_path)
    try:
        with conn:
            _create_schema(conn)
            recipe_id, _, _ = _activate_manifest_recipe(conn)
            _ensure_default_ocr_recipe(conn)
            _ensure_missing_ocr_jobs(conn)
    finally:
        conn.close()

    return LibraryInitResult(
        library_root=str(library_root),
        database_path=str(database_path),
        created_database=created_database,
        created_recipe_id=recipe_id,
    )


def import_folder(
    library_root: Path | str,
    source_folder: Path | str,
    wait_for_permission: Callable[[], None] | None = None,
) -> ImportFolderResult:
    """Import supported files, optionally waiting at each file boundary.

    ``wait_for_permission`` is deliberately checked before a file is read or
    copied.  This lets a UI pause a long import without leaving a partly
    committed asset: a file already being processed finishes atomically, and
    the next file waits.
    """
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    source_root = _resolve(source_folder)
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

    conn = _connect(_database_path(library_root_path))
    try:
        active_recipe_id = _get_active_recipe_id(conn)
        ocr_recipe_id = _ensure_default_ocr_recipe(conn)
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
            content_hash = _compute_sha256(file_path)
            now = _utc_now()

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
                    source_change = _upsert_source_record(
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
            width, height = _safe_image_dimensions_from_bytes(destination_absolute.read_bytes())
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
                source_change = _upsert_source_record(
                    conn=conn,
                    asset_id=asset_id,
                    source_path=str(file_path),
                    now=now,
                )
                if source_change == "added":
                    source_records_added += 1
                else:
                    source_records_refreshed += 1

                jobs_created += _create_job(
                    conn=conn,
                    job_type="generate_thumbnail",
                    asset_id=asset_id,
                    recipe_id=None,
                    payload={
                        "asset_id": asset_id,
                        "library_path": destination_relative.as_posix(),
                    },
                    now=now,
                )
                jobs_created += _create_job(
                    conn=conn,
                    job_type="embed_asset",
                    asset_id=asset_id,
                    recipe_id=active_recipe_id,
                    payload={
                        "asset_id": asset_id,
                        "recipe_id": active_recipe_id,
                        "media_type": media_type,
                        "library_path": destination_relative.as_posix(),
                    },
                    now=now,
                )
                jobs_created += ocr_artifacts.enqueue_ocr_asset_job(
                    conn,
                    asset_id=asset_id,
                    media_type=media_type,
                    library_path=destination_relative.as_posix(),
                    create_job=_create_job,
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


def _compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _upsert_source_record(conn: sqlite3.Connection, asset_id: str, source_path: str, now: str) -> str:
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


def _create_job(
    conn: sqlite3.Connection,
    job_type: str,
    asset_id: str | None,
    recipe_id: str | None,
    payload: dict[str, object],
    now: str,
) -> int:
    return job_queue.create_job(conn, job_type, asset_id, recipe_id, payload, now)


def switch_active_recipe(
    library_root: Path | str,
    preset_key: str,
    gif_frame_count: int | None = None,
) -> SwitchRecipeResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        with conn:
            recipe_id = _ensure_recipe(conn, _recipe_spec_for_preset(preset_key, gif_frame_count))
            _set_active_recipe_id(conn, recipe_id)

            asset_rows = conn.execute(
                """
                SELECT id
                FROM asset
                WHERE deleted_at IS NULL
                ORDER BY imported_at ASC, id ASC
                """
            ).fetchall()

            reindex_jobs_created = 0
            for asset_row in asset_rows:
                asset_id = str(asset_row["id"])
                has_embedding = conn.execute(
                    """
                    SELECT 1
                    FROM embedding_item
                    WHERE asset_id = ?
                      AND recipe_id = ?
                      AND kind = 'image'
                    LIMIT 1
                    """,
                    (asset_id, recipe_id),
                ).fetchone()
                if has_embedding is not None:
                    continue

                if job_queue.has_incomplete_job(
                    conn,
                    asset_id=asset_id,
                    recipe_id=recipe_id,
                    job_type="embed_asset",
                ):
                    continue

                library_path_row = conn.execute(
                    "SELECT library_path, media_type FROM asset WHERE id = ?",
                    (asset_id,),
                ).fetchone()
                reindex_jobs_created += _create_job(
                    conn=conn,
                    job_type="embed_asset",
                    asset_id=asset_id,
                    recipe_id=recipe_id,
                    payload={
                        "asset_id": asset_id,
                        "recipe_id": recipe_id,
                        "media_type": str(library_path_row["media_type"]),
                        "library_path": str(library_path_row["library_path"]),
                    },
                    now=_utc_now(),
                )

        recipe_row = _get_recipe_row(conn, recipe_id)
        return SwitchRecipeResult(
            library_root=str(library_root_path),
            active_recipe_id=recipe_id,
            active_recipe_label=_recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                _gif_frame_count_for_recipe(recipe_row),
            ),
            assets_seen=len(asset_rows),
            reindex_jobs_created=reindex_jobs_created,
        )
    finally:
        conn.close()


def _delete_asset_rows(
    conn: sqlite3.Connection,
    library_root_path: Path,
    asset_id: str,
) -> tuple[int, int, int]:
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


def remove_source_record(
    library_root: Path | str,
    asset_id: str,
    source_path: str,
) -> AssetMutationResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
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

            removed_jobs, removed_renditions, removed_embeddings = _delete_asset_rows(
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
    conn = _connect(_database_path(library_root_path))
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
            removed_jobs, removed_renditions, removed_embeddings = _delete_asset_rows(
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
    requested_asset_ids = _normalize_batch_asset_ids(asset_ids)
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        with conn:
            _require_existing_asset_ids(conn, requested_asset_ids)
            removed_source_records = 0
            removed_jobs = 0
            removed_renditions = 0
            removed_embeddings = 0
            for asset_id in requested_asset_ids:
                removed_source_records += int(
                    conn.execute("SELECT COUNT(*) FROM source_record WHERE asset_id = ?", (asset_id,)).fetchone()[0]
                )
                jobs, renditions, embeddings = _delete_asset_rows(conn, library_root_path, asset_id)
                removed_jobs += jobs
                removed_renditions += renditions
                removed_embeddings += embeddings
        return BatchAssetActionResult(
            library_root=str(library_root_path), action="delete", requested_asset_ids=requested_asset_ids,
            affected_asset_ids=requested_asset_ids, skipped_running_asset_ids=[],
            removed_source_records=removed_source_records, removed_jobs=removed_jobs,
            removed_renditions=removed_renditions, removed_embeddings=removed_embeddings,
            reindex_jobs_created=0,
        )
    finally:
        conn.close()


def rebuild_active_indexes(library_root: Path | str, asset_ids: list[str]) -> BatchAssetActionResult:
    requested_asset_ids = _normalize_batch_asset_ids(asset_ids)
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
    try:
        with conn:
            _require_existing_asset_ids(conn, requested_asset_ids)
            active_recipe_id = _get_active_recipe_id(conn)
            affected_asset_ids: list[str] = []
            skipped_running_asset_ids: list[str] = []
            removed_embeddings = 0
            reindex_jobs_created = 0
            for asset_id in requested_asset_ids:
                running = conn.execute(
                    "SELECT 1 FROM job WHERE asset_id = ? AND recipe_id = ? AND type = 'embed_asset' AND status = 'running' LIMIT 1",
                    (asset_id, active_recipe_id),
                ).fetchone()
                if running is not None:
                    skipped_running_asset_ids.append(asset_id)
                    continue
                removed_embeddings += conn.execute(
                    "DELETE FROM embedding_item WHERE asset_id = ? AND recipe_id = ? AND kind = 'image'",
                    (asset_id, active_recipe_id),
                ).rowcount
                conn.execute(
                    "DELETE FROM job WHERE asset_id = ? AND recipe_id = ? AND type = 'embed_asset' AND status IN ('pending', 'failed')",
                    (asset_id, active_recipe_id),
                )
                asset_row = conn.execute(
                    "SELECT library_path, media_type FROM asset WHERE id = ?", (asset_id,)
                ).fetchone()
                reindex_jobs_created += _create_job(
                    conn, "embed_asset", asset_id, active_recipe_id,
                    {"asset_id": asset_id, "recipe_id": active_recipe_id,
                     "media_type": str(asset_row["media_type"]), "library_path": str(asset_row["library_path"])},
                    _utc_now(),
                )
                affected_asset_ids.append(asset_id)
        return BatchAssetActionResult(
            library_root=str(library_root_path), action="rebuild-active-index",
            requested_asset_ids=requested_asset_ids, affected_asset_ids=affected_asset_ids,
            skipped_running_asset_ids=skipped_running_asset_ids, removed_source_records=0,
            removed_jobs=0, removed_renditions=0, removed_embeddings=removed_embeddings,
            reindex_jobs_created=reindex_jobs_created,
        )
    finally:
        conn.close()


def _normalize_batch_asset_ids(asset_ids: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(str(asset_id).strip() for asset_id in asset_ids if str(asset_id).strip()))
    if not normalized:
        raise ValueError("At least one asset id is required")
    return normalized


def _require_existing_asset_ids(conn: sqlite3.Connection, asset_ids: list[str]) -> None:
    placeholders = ", ".join("?" for _ in asset_ids)
    rows = conn.execute(
        f"SELECT id FROM asset WHERE deleted_at IS NULL AND id IN ({placeholders})", tuple(asset_ids)
    ).fetchall()
    found_ids = {str(row["id"]) for row in rows}
    missing_ids = [asset_id for asset_id in asset_ids if asset_id not in found_ids]
    if missing_ids:
        raise ValueError(f"Unknown asset ids: {', '.join(missing_ids)}")


def retry_failed_jobs(library_root: Path | str) -> RetryJobsResult:
    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
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


def delete_pending_jobs(library_root: Path | str, job_ids: list[str]) -> DeletePendingJobsResult:
    """Delete only queue records that have not been claimed by a worker.

    Assets and generated library files are intentionally untouched.  A Job
    that becomes running after the user selected it is reported as skipped.
    """
    unique_job_ids = list(dict.fromkeys(str(job_id) for job_id in job_ids if str(job_id)))
    if not unique_job_ids:
        raise ValueError("At least one pending job id is required")

    init_result = initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    conn = _connect(_database_path(library_root_path))
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
                int(row["gif_frame_count"]) if row["gif_frame_count"] is not None else DEFAULT_GIF_FRAME_COUNT,
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
                int(row["gif_frame_count"]) if row["gif_frame_count"] is not None else DEFAULT_GIF_FRAME_COUNT,
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


def get_setup_state(library_root: Path | str) -> SetupStateResult:
    from .runtime_service import get_setup_state as _get_setup_state

    return _get_setup_state(library_root)


def _project_asset_status(
    active_recipe_id: str,
    embeddings: list[sqlite3.Row],
    jobs: list[sqlite3.Row],
) -> str:
    has_active_embedding = any(str(row["recipe_id"]) == active_recipe_id for row in embeddings)
    if has_active_embedding:
        return "indexed"

    active_jobs = [
        row for row in jobs if str(row["type"]) == "embed_asset" and str(row["recipe_id"]) == active_recipe_id
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
