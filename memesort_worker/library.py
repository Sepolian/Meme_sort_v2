from __future__ import annotations

from dataclasses import asdict, dataclass


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
