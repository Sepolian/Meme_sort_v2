from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from . import library_internal as library
from . import job_queue
from . import ocr_artifacts
from .embedding_backend import EmbeddingBackend, EmbeddingBackendError
from .ocr_backend import OcrBackend, get_ocr_backend
from .semantic_retrieval import vector_to_blob


def run_pending_jobs(
    library_root: Path | str,
    max_jobs: int | None = None,
) -> library.RunJobsResult:
    init_result = library.initialize_library(library_root)
    library_root_path = Path(init_result.library_root)
    backend: EmbeddingBackend | None = None
    ocr_backend: OcrBackend | None = None
    conn = library._connect(library._database_path(library_root_path))
    try:
        with conn:
            requeued_running_jobs, retried_failed_jobs = job_queue.requeue_incomplete_jobs(conn)
        job_rows = job_queue.fetch_pending_jobs(conn, max_jobs=max_jobs)

        processed_jobs = 0
        completed_jobs = 0
        failed_jobs = 0
        skipped_jobs = 0

        for job_row in job_rows:
            processed_jobs += 1
            job_id = str(job_row["id"])
            job_type = str(job_row["type"])
            payload = job_queue.payload_for_job(job_row)

            try:
                with conn:
                    claimed = job_queue.mark_job_running(conn, job_id)
                if not claimed:
                    skipped_jobs += 1
                    continue

                if job_type == "generate_thumbnail":
                    _run_generate_thumbnail_job(conn, library_root_path, payload)
                elif job_type == "embed_asset":
                    if backend is None:
                        backend = library.get_embedding_backend()
                    _run_embed_asset_job(conn, library_root_path, payload, backend)
                elif job_type == "ocr_asset":
                    if ocr_backend is None:
                        ocr_backend = get_ocr_backend(library_root_path, "llama.cpp")
                    ocr_artifacts.run_ocr_asset_job(
                        conn,
                        library_root_path,
                        payload,
                        ocr_backend,
                    )
                else:
                    skipped_jobs += 1
                    raise ValueError(f"Unsupported job type: {job_type}")

                with conn:
                    job_queue.mark_job_completed(conn, job_id)
                    completed_jobs += 1
            except Exception as exc:
                with conn:
                    job_queue.mark_job_failed(
                        conn,
                        job_id,
                        error_code=type(exc).__name__,
                        error_detail=str(exc),
                    )
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
        conn.close()


def _run_generate_thumbnail_job(
    conn: sqlite3.Connection,
    library_root_path: Path,
    payload: dict[str, object],
) -> None:
    asset_id = str(payload["asset_id"])
    asset_row = conn.execute(
        """
        SELECT library_path
        FROM asset
        WHERE id = ?
          AND deleted_at IS NULL
        """,
        (asset_id,),
    ).fetchone()
    if asset_row is None:
        raise ValueError(f"Asset not found for thumbnail job: {asset_id}")

    source_path = library_root_path / str(asset_row["library_path"])
    thumb_path = library_root_path / "thumbnails" / f"{asset_id}.jpg"
    with Image.open(source_path) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((256, 256), Image.Resampling.LANCZOS)
        rgb.save(thumb_path, format="JPEG", quality=90)
        width, height = rgb.size

    existing_rendition = conn.execute(
        """
        SELECT id
        FROM rendition
        WHERE asset_id = ?
          AND kind = 'thumbnail'
        """,
        (asset_id,),
    ).fetchone()
    if existing_rendition is None:
        conn.execute(
            """
            INSERT INTO rendition (id, asset_id, kind, path, width, height, frame_index, created_at)
            VALUES (?, ?, 'thumbnail', ?, ?, ?, NULL, ?)
            """,
            (
                str(uuid.uuid4()),
                asset_id,
                f"thumbnails/{asset_id}.jpg",
                width,
                height,
                library._utc_now(),
            ),
        )
    else:
        conn.execute(
            """
            UPDATE rendition
            SET path = ?, width = ?, height = ?, created_at = ?
            WHERE id = ?
            """,
            (
                f"thumbnails/{asset_id}.jpg",
                width,
                height,
                library._utc_now(),
                str(existing_rendition["id"]),
            ),
        )
    conn.execute(
        """
        UPDATE asset
        SET updated_at = ?
        WHERE id = ?
        """,
        (library._utc_now(), asset_id),
    )


def _run_embed_asset_job(
    conn: sqlite3.Connection,
    library_root_path: Path,
    payload: dict[str, object],
    backend: EmbeddingBackend,
) -> None:
    asset_id = str(payload["asset_id"])
    recipe_id = str(payload["recipe_id"])
    recipe_row = library._get_recipe_row(conn, recipe_id)
    asset_row = conn.execute(
        """
        SELECT library_path
        FROM asset
        WHERE id = ?
          AND deleted_at IS NULL
        """,
        (asset_id,),
    ).fetchone()
    if asset_row is None:
        raise ValueError(f"Asset not found for embed job: {asset_id}")

    existing = conn.execute(
        """
        SELECT id
        FROM embedding_item
        WHERE asset_id = ?
          AND recipe_id = ?
          AND kind = 'image'
        LIMIT 1
        """,
        (asset_id, recipe_id),
    ).fetchone()
    if existing is not None:
        return

    image_path = library_root_path / str(asset_row["library_path"])
    image_bytes = image_path.read_bytes()
    output_dimension = int(recipe_row["output_dimension"])
    instruction = library._instruction_text_for_key(str(recipe_row["instruction_key"]))
    preprocess_version = str(recipe_row["preprocess_version"])
    gif_frame_count = library._gif_frame_count_for_recipe(recipe_row)

    if str(payload.get("media_type")) == "image/gif":
        frame_payloads = library._extract_gif_frame_bytes(
            image_bytes,
            preprocess_version,
            frame_count=gif_frame_count,
        )
        for frame_index, frame_bytes in frame_payloads:
            vector = backend.embed_image_bytes(
                frame_bytes,
                output_dimension,
                instruction=instruction,
            )
            _insert_embedding_item(conn, asset_id, recipe_id, output_dimension, f"frame:{frame_index}", vector)
    else:
        processed_image_bytes = library._preprocess_image_bytes(
            image_bytes,
            preprocess_version,
        )
        vector = backend.embed_image_bytes(
            processed_image_bytes,
            output_dimension,
            instruction=instruction,
        )
        _insert_embedding_item(conn, asset_id, recipe_id, output_dimension, "original", vector)

    conn.execute(
        """
        UPDATE asset
        SET updated_at = ?
        WHERE id = ?
        """,
        (library._utc_now(), asset_id),
    )


def _insert_embedding_item(
    conn: sqlite3.Connection,
    asset_id: str,
    recipe_id: str,
    output_dimension: int,
    source_ref: str,
    vector: np.ndarray,
) -> None:
    if vector.shape[0] != output_dimension:
        raise EmbeddingBackendError(
            f"Embedding backend returned dim {vector.shape[0]}, expected {output_dimension}"
        )
    conn.execute(
        """
        INSERT INTO embedding_item (
            id,
            asset_id,
            recipe_id,
            kind,
            source_ref,
            vector_dim,
            vector_blob,
            created_at
        )
        VALUES (?, ?, ?, 'image', ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            asset_id,
            recipe_id,
            source_ref,
            output_dimension,
            sqlite3.Binary(vector_to_blob(vector)),
            library._utc_now(),
        ),
    )
