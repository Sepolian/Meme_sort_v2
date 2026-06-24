from __future__ import annotations

"""
PROTOTYPE ONLY.

Question this prototype is answering:
Does the Milestone 1 state model for library import, duplicate coalescing,
recipe switching, job failure/retry, and source-record deletion feel right
when pushed through concrete cases by hand?
"""

from copy import deepcopy
from dataclasses import dataclass, field


@dataclass
class Recipe:
    recipe_id: str
    label: str
    model_id: str
    output_dimension: int


@dataclass
class SourceFile:
    path: str
    content_hash: str
    media_type: str
    supported: bool


@dataclass
class SourceRecord:
    path: str
    seen_count: int = 1


@dataclass
class EmbeddingRecord:
    recipe_id: str
    vector_dim: int


@dataclass
class Asset:
    asset_id: str
    content_hash: str
    media_type: str
    library_path: str
    source_records: list[SourceRecord] = field(default_factory=list)
    thumbnail_ready: bool = False
    embeddings: list[EmbeddingRecord] = field(default_factory=list)


@dataclass
class Job:
    job_id: str
    job_type: str
    asset_id: str
    recipe_id: str | None
    status: str = "pending"
    attempt_count: int = 0


@dataclass
class DeletedAsset:
    asset_id: str
    content_hash: str
    removed_source_count: int
    removed_job_count: int
    removed_embedding_count: int
    reason: str


@dataclass
class PrototypeState:
    active_recipe_id: str
    recipes: dict[str, Recipe]
    source_pool: list[SourceFile]
    assets: list[Asset]
    jobs: list[Job]
    deleted_assets: list[DeletedAsset]
    event_log: list[str]
    next_asset_number: int
    next_job_number: int


def initial_state() -> PrototypeState:
    recipes = {
        "recipe-2b": Recipe(
            recipe_id="recipe-2b",
            label="Qwen 2B / 2048d",
            model_id="Qwen/Qwen3-VL-Embedding-2B",
            output_dimension=2048,
        ),
        "recipe-8b": Recipe(
            recipe_id="recipe-8b",
            label="Qwen 8B / 4096d",
            model_id="Qwen/Qwen3-VL-Embedding-8B",
            output_dimension=4096,
        ),
    }
    return PrototypeState(
        active_recipe_id="recipe-2b",
        recipes=recipes,
        source_pool=[],
        assets=[],
        jobs=[],
        deleted_assets=[],
        event_log=["State reset."],
        next_asset_number=1,
        next_job_number=1,
    )


def seed_demo_sources(state: PrototypeState) -> PrototypeState:
    next_state = _clone(state)
    next_state.source_pool = [
        SourceFile(
            path=r"C:\demo\cats\confused-cat.jpg",
            content_hash="sha256-cat-01",
            media_type="image/jpeg",
            supported=True,
        ),
        SourceFile(
            path=r"C:\demo\copies\confused-cat-copy.jpg",
            content_hash="sha256-cat-01",
            media_type="image/jpeg",
            supported=True,
        ),
        SourceFile(
            path=r"C:\demo\memes\drake-hotline.png",
            content_hash="sha256-drake-02",
            media_type="image/png",
            supported=True,
        ),
        SourceFile(
            path=r"C:\demo\docs\notes.txt",
            content_hash="sha256-text-03",
            media_type="text/plain",
            supported=False,
        ),
    ]
    _push_event(next_state, "Loaded demo source files: 3 supported, 1 unsupported.")
    return next_state


def import_source_pool(state: PrototypeState) -> PrototypeState:
    next_state = _clone(state)
    if not next_state.source_pool:
        _push_event(next_state, "Import skipped: source pool is empty.")
        return next_state

    supported_count = 0
    new_assets = 0
    duplicate_hits = 0
    refreshed_sources = 0
    ignored_files = 0

    for source in next_state.source_pool:
        if not source.supported:
            ignored_files += 1
            continue

        supported_count += 1
        asset = _find_live_asset_by_hash(next_state, source.content_hash)
        if asset is None:
            asset_id = _next_asset_id(next_state)
            library_path = _build_library_path(asset_id, source.media_type)
            asset = Asset(
                asset_id=asset_id,
                content_hash=source.content_hash,
                media_type=source.media_type,
                library_path=library_path,
                source_records=[SourceRecord(path=source.path)],
            )
            next_state.assets.append(asset)
            _enqueue_job(next_state, "generate_thumbnail", asset.asset_id, None)
            _enqueue_job(next_state, "embed_asset", asset.asset_id, next_state.active_recipe_id)
            new_assets += 1
            continue

        duplicate_hits += 1
        source_record = _find_source_record(asset, source.path)
        if source_record is None:
            asset.source_records.append(SourceRecord(path=source.path))
        else:
            source_record.seen_count += 1
            refreshed_sources += 1

    _push_event(
        next_state,
        (
            "Imported source pool: "
            f"supported={supported_count}, new_assets={new_assets}, "
            f"duplicate_hits={duplicate_hits}, refreshed_sources={refreshed_sources}, "
            f"ignored={ignored_files}."
        ),
    )
    return next_state


def complete_next_pending_job(state: PrototypeState) -> PrototypeState:
    next_state = _clone(state)
    job = _first_job(next_state, status="pending")
    if job is None:
        _push_event(next_state, "No pending jobs to complete.")
        return next_state

    job.status = "completed"
    asset = _find_asset(next_state, job.asset_id)
    if asset is None:
        _push_event(next_state, f"Completed orphaned job {job.job_id}; asset was already deleted.")
        return next_state

    if job.job_type == "generate_thumbnail":
        asset.thumbnail_ready = True
        _push_event(next_state, f"Completed thumbnail job {job.job_id} for {asset.asset_id}.")
        return next_state

    recipe = next_state.recipes[job.recipe_id or next_state.active_recipe_id]
    if _find_embedding(asset, recipe.recipe_id) is None:
        asset.embeddings.append(
            EmbeddingRecord(recipe_id=recipe.recipe_id, vector_dim=recipe.output_dimension)
        )
    _push_event(
        next_state,
        f"Completed embedding job {job.job_id} for {asset.asset_id} under {recipe.label}.",
    )
    return next_state


def fail_next_pending_embedding_job(state: PrototypeState) -> PrototypeState:
    next_state = _clone(state)
    job = _first_job(next_state, status="pending", job_type="embed_asset")
    if job is None:
        _push_event(next_state, "No pending embedding job to fail.")
        return next_state

    job.status = "failed"
    _push_event(next_state, f"Failed embedding job {job.job_id} for {job.asset_id}.")
    return next_state


def retry_failed_jobs(state: PrototypeState) -> PrototypeState:
    next_state = _clone(state)
    retried = 0
    for job in next_state.jobs:
        if job.status != "failed":
            continue
        job.status = "pending"
        job.attempt_count += 1
        retried += 1

    if retried == 0:
        _push_event(next_state, "No failed jobs to retry.")
    else:
        _push_event(next_state, f"Retried {retried} failed job(s).")
    return next_state


def switch_active_recipe(state: PrototypeState) -> PrototypeState:
    next_state = _clone(state)
    next_state.active_recipe_id = (
        "recipe-8b" if next_state.active_recipe_id == "recipe-2b" else "recipe-2b"
    )
    recipe = next_state.recipes[next_state.active_recipe_id]

    scheduled = 0
    for asset in next_state.assets:
        if _find_embedding(asset, recipe.recipe_id) is not None:
            continue
        if _has_job(asset.asset_id, recipe.recipe_id, next_state.jobs):
            continue
        _enqueue_job(next_state, "embed_asset", asset.asset_id, recipe.recipe_id)
        scheduled += 1

    _push_event(
        next_state,
        f"Switched active recipe to {recipe.label}; scheduled {scheduled} reindex job(s).",
    )
    return next_state


def remove_source_record(state: PrototypeState, asset_index: int, source_index: int) -> PrototypeState:
    next_state = _clone(state)
    if asset_index < 0 or asset_index >= len(next_state.assets):
        _push_event(next_state, f"Invalid asset index: {asset_index}.")
        return next_state

    asset = next_state.assets[asset_index]
    if source_index < 0 or source_index >= len(asset.source_records):
        _push_event(next_state, f"Invalid source index: {source_index} for {asset.asset_id}.")
        return next_state

    removed = asset.source_records.pop(source_index)
    if asset.source_records:
        _push_event(
            next_state,
            f"Removed source record {removed.path} from {asset.asset_id}; asset remains live.",
        )
        return next_state

    removed_jobs = len([job for job in next_state.jobs if job.asset_id == asset.asset_id])
    removed_embeddings = len(asset.embeddings)
    next_state.jobs = [job for job in next_state.jobs if job.asset_id != asset.asset_id]
    next_state.deleted_assets.append(
        DeletedAsset(
            asset_id=asset.asset_id,
            content_hash=asset.content_hash,
            removed_source_count=1,
            removed_job_count=removed_jobs,
            removed_embedding_count=removed_embeddings,
            reason="Last source record removed.",
        )
    )
    next_state.assets.pop(asset_index)
    _push_event(
        next_state,
        f"Removed final source record from {asset.asset_id}; asset deleted with derived artifacts.",
    )
    return next_state


def renderable_state(state: PrototypeState) -> dict[str, object]:
    active_recipe = state.recipes[state.active_recipe_id]
    return {
        "active_recipe": {
            "recipe_id": active_recipe.recipe_id,
            "label": active_recipe.label,
            "model_id": active_recipe.model_id,
            "output_dimension": active_recipe.output_dimension,
        },
        "source_pool": [
            {
                "index": index,
                "path": source.path,
                "content_hash": source.content_hash,
                "supported": source.supported,
                "media_type": source.media_type,
            }
            for index, source in enumerate(state.source_pool)
        ],
        "assets": [
            {
                "index": index,
                "asset_id": asset.asset_id,
                "content_hash": asset.content_hash,
                "library_path": asset.library_path,
                "media_type": asset.media_type,
                "thumbnail_ready": asset.thumbnail_ready,
                "active_status": _active_asset_status(asset, state),
                "source_records": [
                    {
                        "index": source_index,
                        "path": record.path,
                        "seen_count": record.seen_count,
                    }
                    for source_index, record in enumerate(asset.source_records)
                ],
                "indexed_recipes": [
                    state.recipes[embedding.recipe_id].label for embedding in asset.embeddings
                ],
                "stale_recipes": [
                    state.recipes[embedding.recipe_id].label
                    for embedding in asset.embeddings
                    if embedding.recipe_id != state.active_recipe_id
                ],
            }
            for index, asset in enumerate(state.assets)
        ],
        "jobs": [
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "asset_id": job.asset_id,
                "recipe": state.recipes[job.recipe_id].label if job.recipe_id else None,
                "status": job.status,
                "attempt_count": job.attempt_count,
            }
            for job in state.jobs
        ],
        "deleted_assets": [
            {
                "asset_id": deleted.asset_id,
                "content_hash": deleted.content_hash,
                "removed_job_count": deleted.removed_job_count,
                "removed_embedding_count": deleted.removed_embedding_count,
                "reason": deleted.reason,
            }
            for deleted in state.deleted_assets
        ],
        "recent_events": state.event_log[-8:],
    }


def _clone(state: PrototypeState) -> PrototypeState:
    return deepcopy(state)


def _push_event(state: PrototypeState, message: str) -> None:
    state.event_log.append(message)


def _find_live_asset_by_hash(state: PrototypeState, content_hash: str) -> Asset | None:
    for asset in state.assets:
        if asset.content_hash == content_hash:
            return asset
    return None


def _find_source_record(asset: Asset, path: str) -> SourceRecord | None:
    for record in asset.source_records:
        if record.path == path:
            return record
    return None


def _build_library_path(asset_id: str, media_type: str) -> str:
    extension_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    extension = extension_map.get(media_type, ".bin")
    return f"originals/{asset_id}{extension}"


def _next_asset_id(state: PrototypeState) -> str:
    value = f"A{state.next_asset_number}"
    state.next_asset_number += 1
    return value


def _next_job_id(state: PrototypeState) -> str:
    value = f"J{state.next_job_number}"
    state.next_job_number += 1
    return value


def _enqueue_job(state: PrototypeState, job_type: str, asset_id: str, recipe_id: str | None) -> None:
    state.jobs.append(
        Job(
            job_id=_next_job_id(state),
            job_type=job_type,
            asset_id=asset_id,
            recipe_id=recipe_id,
        )
    )


def _first_job(
    state: PrototypeState,
    status: str,
    job_type: str | None = None,
) -> Job | None:
    for job in state.jobs:
        if job.status != status:
            continue
        if job_type is not None and job.job_type != job_type:
            continue
        return job
    return None


def _find_asset(state: PrototypeState, asset_id: str) -> Asset | None:
    for asset in state.assets:
        if asset.asset_id == asset_id:
            return asset
    return None


def _find_embedding(asset: Asset, recipe_id: str) -> EmbeddingRecord | None:
    for embedding in asset.embeddings:
        if embedding.recipe_id == recipe_id:
            return embedding
    return None


def _has_job(asset_id: str, recipe_id: str, jobs: list[Job]) -> bool:
    for job in jobs:
        if job.asset_id != asset_id:
            continue
        if job.recipe_id != recipe_id:
            continue
        if job.status in {"pending", "failed"}:
            return True
    return False


def _active_asset_status(asset: Asset, state: PrototypeState) -> str:
    if _find_embedding(asset, state.active_recipe_id) is not None:
        return "indexed"

    for job in state.jobs:
        if job.asset_id != asset.asset_id:
            continue
        if job.recipe_id != state.active_recipe_id:
            continue
        if job.status == "failed":
            return "failed"
        if job.status == "pending":
            return "pending"

    return "missing"
