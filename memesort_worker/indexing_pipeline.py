from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from . import asset_catalog
from . import asset_preprocessing
from . import library
from . import job_queue
from .embedding_backend import EmbeddingBackend
from .indexing_store import IndexingStore, RecipeRow
from .ocr_backend import OcrBackend
from .recipe_provider import RuntimeRecipeProvider, default_provider, resolve_gif_frame_count


class IndexingRuntime(Protocol):
    """The smallest runtime surface the indexing pipeline depends on."""

    def is_ready_for_indexing(self) -> tuple[bool, str]:
        ...

    def get_embedding_backend(self) -> EmbeddingBackend:
        ...

    def get_ocr_backend(self) -> OcrBackend:
        ...


def run_pending_jobs(
    library_root: Path | str,
    runtime: IndexingRuntime,
    max_jobs: int | None = None,
    provider: RuntimeRecipeProvider | None = None,
) -> library.RunJobsResult:
    runtime_ready, runtime_message = runtime.is_ready_for_indexing()
    if not runtime_ready:
        raise RuntimeError(
            "Vulkan runtime is not authorized for indexing in this app session: "
            f"{runtime_message}"
        )
    init_result = asset_catalog.initialize_library(library_root, provider)
    library_root_path = Path(init_result.library_root)
    backend: EmbeddingBackend | None = None
    ocr_backend: OcrBackend | None = None
    store = IndexingStore(library_root_path)
    try:
        (
            requeued_running_jobs,
            retried_failed_jobs,
            pending_jobs,
        ) = store.prepare_jobs(max_jobs)

        processed_jobs = 0
        completed_jobs = 0
        failed_jobs = 0
        skipped_jobs = 0

        for job in pending_jobs:
            processed_jobs += 1

            try:
                if not store.claim_job(job):
                    skipped_jobs += 1
                    continue

                if job.job_type is job_queue.JobType.GENERATE_THUMBNAIL:
                    _run_generate_thumbnail_job(store, library_root_path, job.payload)
                elif job.job_type is job_queue.JobType.EMBED_ASSET:
                    if backend is None:
                        backend = runtime.get_embedding_backend()
                    _run_embed_asset_job(store, library_root_path, job.payload, backend, provider)
                elif job.job_type is job_queue.JobType.OCR_ASSET:
                    if ocr_backend is None:
                        ocr_backend = runtime.get_ocr_backend()
                    store.run_ocr_job(job.payload, ocr_backend)
                else:
                    skipped_jobs += 1
                    raise ValueError(f"Unsupported job type: {job.job_type}")

                store.complete_job(job)
                completed_jobs += 1
            except Exception as exc:
                store.fail_job(job, exc)
                failed_jobs += 1

        return library.RunJobsResult(
            library_root=str(library_root_path),
            backend=backend.backend_id if backend is not None else "llama.cpp",
            requeued_running_jobs=requeued_running_jobs,
            retried_failed_jobs=retried_failed_jobs,
            processed_jobs=processed_jobs,
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
            skipped_jobs=skipped_jobs,
        )
    finally:
        if ocr_backend is not None:
            ocr_backend.close()
        store.close()


def _run_generate_thumbnail_job(
    store: IndexingStore,
    library_root_path: Path,
    payload: dict[str, object],
) -> None:
    asset_id = str(payload["asset_id"])
    source_path = store.get_asset_library_path(asset_id)
    if source_path is None:
        raise ValueError(f"Asset not found for thumbnail job: {asset_id}")

    thumb_path = library_root_path / "thumbnails" / f"{asset_id}.jpg"
    with Image.open(source_path) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((256, 256), Image.Resampling.LANCZOS)
        rgb.save(thumb_path, format="JPEG", quality=90)
        width, height = rgb.size

    store.upsert_thumbnail_rendition(asset_id, width, height)


def _gif_frame_count_for_recipe(
    recipe: RecipeRow,
    provider: RuntimeRecipeProvider | None = None,
) -> int:
    return resolve_gif_frame_count(recipe.gif_frame_count, recipe.recipe_id, provider)


def _run_embed_asset_job(
    store: IndexingStore,
    library_root_path: Path,
    payload: dict[str, object],
    backend: EmbeddingBackend,
    provider: RuntimeRecipeProvider | None = None,
) -> None:
    asset_id = str(payload["asset_id"])
    recipe_id = str(payload["recipe_id"])
    recipe = store.get_recipe(recipe_id)
    image_path = store.get_asset_library_path(asset_id)
    if image_path is None:
        raise ValueError(f"Asset not found for embed job: {asset_id}")

    if store.has_embedding(asset_id, recipe_id):
        return

    provider = provider or default_provider()
    image_bytes = image_path.read_bytes()
    output_dimension = recipe.output_dimension
    instruction = provider.instruction_text_for_key(recipe.instruction_key)
    gif_frame_count = _gif_frame_count_for_recipe(recipe, provider)
    spec = provider.preprocess_spec_for_version(recipe.preprocess_version)

    if str(payload.get("media_type")) == "image/gif":
        frame_payloads = asset_preprocessing.extract_gif_frame_bytes(
            image_bytes,
            spec,
            frame_count=gif_frame_count,
        )
        for frame_index, frame_bytes in frame_payloads:
            vector = backend.embed_image_bytes(
                frame_bytes,
                output_dimension,
                instruction=instruction,
            )
            store.insert_embedding(asset_id, recipe_id, output_dimension, f"frame:{frame_index}", vector)
    else:
        processed_image_bytes = asset_preprocessing.preprocess_image_bytes(
            image_bytes,
            spec,
        )
        vector = backend.embed_image_bytes(
            processed_image_bytes,
            output_dimension,
            instruction=instruction,
        )
        store.insert_embedding(asset_id, recipe_id, output_dimension, "original", vector)

    store.touch_asset(asset_id)
