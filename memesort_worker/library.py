from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import asset_catalog
from . import asset_preprocessing
from . import ocr_artifacts
from .import_contracts import (
    ImportBatchError,
    ImportBatchErrorCode,
    ImportBatchPreflightError,
    ImportBatchResult,
    ImportFailure,
    ImportFailureCode,
    ImportFailureStage,
    MAX_IMPORT_FAILURE_DETAILS,
)
from .recipe_provider import RuntimeRecipeProvider, default_provider


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


def _safe_image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int | None, int | None]:
    return asset_preprocessing.safe_image_dimensions_from_bytes(image_bytes)


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


def retry_failed_jobs(library_root: Path | str) -> RetryJobsResult:
    return asset_catalog.retry_failed_jobs(library_root)


def delete_pending_jobs(library_root: Path | str, job_ids: list[str]) -> DeletePendingJobsResult:
    """Delete only queue records that have not been claimed by a worker.

    Assets and generated library files are intentionally untouched.  A Job
    that becomes running after the user selected it is reported as skipped.
    """
    return asset_catalog.delete_pending_jobs(library_root, job_ids)


def list_assets(library_root: Path | str) -> AssetListResult:
    """Deprecated: use LibraryStore.list_assets_detailed()."""
    from .library_store import LibraryStore

    with LibraryStore(library_root) as store:
        return store.list_assets_detailed()


def get_asset_detail(
    library_root: Path | str,
    asset_id: str,
) -> AssetDetailResult:
    """Deprecated: use LibraryStore.get_asset_detail()."""
    from .library_store import LibraryStore

    with LibraryStore(library_root) as store:
        return store.get_asset_detail(asset_id)


def scan_duplicate_assets(
    library_root: Path | str,
    threshold: float = 0.92,
) -> DuplicateScanResult:
    """Deprecated: use LibraryStore.scan_duplicate_assets()."""
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    from .library_store import LibraryStore

    with LibraryStore(library_root) as store:
        return store.scan_duplicate_assets(threshold)


def get_library_status(library_root: Path | str) -> LibraryStatusResult:
    """Deprecated: use LibraryStore.get_library_status()."""
    from .library_store import LibraryStore

    with LibraryStore(library_root) as store:
        return store.get_library_status()


def search_text(
    library_root: Path | str,
    query: str,
    top_k: int = 10,
    *,
    runtime,
) -> SearchResult:
    from .retrieval_service import search_text as _search_text

    return _search_text(
        library_root,
        query=query,
        top_k=top_k,
        runtime=runtime,
    )


def search_image_path(
    library_root: Path | str,
    image_path: Path | str,
    top_k: int = 10,
    *,
    runtime,
) -> ImageSearchResult:
    from .retrieval_service import search_image_path as _search_image_path

    return _search_image_path(
        library_root,
        image_path=image_path,
        top_k=top_k,
        runtime=runtime,
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
