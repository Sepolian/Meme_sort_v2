from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import job_queue

DEFAULT_OCR_RECIPE = {
    "engine": "paddleocr",
    "engine_version": "3.6.0",
    "model_key": "PP-OCRv5",
    "language_hint": "ch",
    "preprocess_version": "original-still-v1",
    "min_confidence": 0.5,
}

OCR_ASSET_JOB_TYPE = job_queue.JobType.OCR_ASSET.value
GIF_MEDIA_TYPE = "image/gif"


class OcrBackend(Protocol):
    def recognize_image(self, image_path: Path) -> dict[str, object]:
        ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ocr_recipe (
            id TEXT PRIMARY KEY,
            engine TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            model_key TEXT NOT NULL,
            language_hint TEXT NOT NULL,
            preprocess_version TEXT NOT NULL,
            min_confidence REAL NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_ocr_recipe_identity
        ON ocr_recipe (
            engine,
            engine_version,
            model_key,
            language_hint,
            preprocess_version,
            min_confidence
        );

        CREATE TABLE IF NOT EXISTS ocr_result (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
            ocr_recipe_id TEXT NOT NULL REFERENCES ocr_recipe(id) ON DELETE CASCADE,
            engine TEXT NOT NULL,
            text TEXT NOT NULL,
            searchable_text TEXT NOT NULL,
            confidence REAL,
            language_hint TEXT,
            line_json TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(asset_id, ocr_recipe_id)
        );

        CREATE INDEX IF NOT EXISTS ix_ocr_result_asset_recipe
        ON ocr_result (asset_id, ocr_recipe_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS ocr_result_fts
        USING fts5(
            result_id UNINDEXED,
            asset_id UNINDEXED,
            searchable_text,
            tokenize='trigram'
        );
        """
    )


def ensure_ocr_recipe(conn: sqlite3.Connection, recipe_spec: dict[str, object]) -> str:
    row = conn.execute(
        """
        SELECT id
        FROM ocr_recipe
        WHERE engine = ?
          AND engine_version = ?
          AND model_key = ?
          AND language_hint = ?
          AND preprocess_version = ?
          AND min_confidence = ?
        """,
        (
            recipe_spec["engine"],
            recipe_spec["engine_version"],
            recipe_spec["model_key"],
            recipe_spec["language_hint"],
            recipe_spec["preprocess_version"],
            float(recipe_spec["min_confidence"]),
        ),
    ).fetchone()
    if row is not None:
        return str(row["id"])

    recipe_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO ocr_recipe (
            id,
            engine,
            engine_version,
            model_key,
            language_hint,
            preprocess_version,
            min_confidence,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe_id,
            recipe_spec["engine"],
            recipe_spec["engine_version"],
            recipe_spec["model_key"],
            recipe_spec["language_hint"],
            recipe_spec["preprocess_version"],
            float(recipe_spec["min_confidence"]),
            _utc_now(),
        ),
    )
    return recipe_id


def ensure_default_ocr_recipe(conn: sqlite3.Connection) -> str:
    return ensure_ocr_recipe(conn, DEFAULT_OCR_RECIPE)


def get_ocr_recipe_row(conn: sqlite3.Connection, ocr_recipe_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, engine, engine_version, model_key, language_hint, preprocess_version, min_confidence
        FROM ocr_recipe
        WHERE id = ?
        """,
        (ocr_recipe_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown OCR recipe id: {ocr_recipe_id}")
    return row


def enqueue_ocr_asset_job(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    media_type: str,
    now: str | None = None,
    ocr_recipe_id: str | None = None,
) -> int:
    if media_type == GIF_MEDIA_TYPE:
        return 0

    recipe_id = ocr_recipe_id or ensure_default_ocr_recipe(conn)
    return job_queue.enqueue_ocr(
        conn,
        asset_id=asset_id,
        ocr_recipe_id=recipe_id,
        media_type=media_type,
        now=now or _utc_now(),
    )


def ensure_missing_ocr_jobs(
    conn: sqlite3.Connection,
    *,
    now: str | None = None,
) -> int:
    ocr_recipe_id = ensure_default_ocr_recipe(conn)
    enqueue_time = now or _utc_now()
    asset_rows = conn.execute(
        """
        SELECT id, media_type
        FROM asset
        WHERE deleted_at IS NULL
          AND media_type <> 'image/gif'
        ORDER BY imported_at ASC, id ASC
        """
    ).fetchall()

    jobs_created = 0
    for asset_row in asset_rows:
        asset_id = str(asset_row["id"])
        existing_ocr_result = conn.execute(
            """
            SELECT text
            FROM ocr_result
            WHERE asset_id = ?
              AND ocr_recipe_id = ?
            LIMIT 1
            """,
            (asset_id, ocr_recipe_id),
        ).fetchone()
        # U+FFFD means bytes were already lost while crossing the historical
        # Windows worker pipe.  Treat that result as missing so initialization
        # schedules a one-time repair with the corrected UTF-8 protocol.
        if (
            existing_ocr_result is not None
            and "\ufffd" not in str(existing_ocr_result["text"])
        ):
            continue

        if job_queue.has_incomplete_job(
            conn,
            asset_id=asset_id,
            job_type=OCR_ASSET_JOB_TYPE,
        ):
            continue

        jobs_created += enqueue_ocr_asset_job(
            conn,
            asset_id=asset_id,
            media_type=str(asset_row["media_type"]),
            now=enqueue_time,
            ocr_recipe_id=ocr_recipe_id,
        )
    return jobs_created


def searchable_ocr_text(lines: list[dict[str, object]], min_confidence: float) -> str:
    searchable_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        score_value = line.get("confidence")
        score = float(score_value) if score_value is not None else 1.0
        if score < min_confidence:
            continue
        key = "".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        searchable_lines.append(text)
    return "\n".join(searchable_lines)


def store_ocr_result(
    conn: sqlite3.Connection,
    asset_id: str,
    ocr_recipe_id: str,
    ocr_output: dict[str, object],
) -> str:
    recipe_row = get_ocr_recipe_row(conn, ocr_recipe_id)
    raw_texts = [str(text) for text in ocr_output.get("texts", [])]
    raw_scores = [float(score) for score in ocr_output.get("scores", [])]
    raw_boxes = list(ocr_output.get("boxes", []))
    lines: list[dict[str, object]] = []
    for index, text in enumerate(raw_texts):
        confidence = raw_scores[index] if index < len(raw_scores) else None
        box = raw_boxes[index] if index < len(raw_boxes) else []
        lines.append(
            {
                "text": text,
                "confidence": confidence,
                "bbox": box,
            }
        )

    min_confidence = float(recipe_row["min_confidence"])
    text = str(ocr_output.get("text") or "\n".join(raw_texts)).strip()
    searchable_text = searchable_ocr_text(lines, min_confidence)
    if not searchable_text and text:
        searchable_text = text
    confidence = sum(raw_scores) / len(raw_scores) if raw_scores else None
    engine = str(ocr_output.get("engine") or recipe_row["engine"])
    language_hint = str(ocr_output.get("language_hint") or recipe_row["language_hint"])
    now = _utc_now()

    existing = conn.execute(
        """
        SELECT id
        FROM ocr_result
        WHERE asset_id = ?
          AND ocr_recipe_id = ?
        """,
        (asset_id, ocr_recipe_id),
    ).fetchone()
    result_id = str(existing["id"]) if existing is not None else str(uuid.uuid4())
    if existing is None:
        conn.execute(
            """
            INSERT INTO ocr_result (
                id,
                asset_id,
                ocr_recipe_id,
                engine,
                text,
                searchable_text,
                confidence,
                language_hint,
                line_json,
                bbox_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                asset_id,
                ocr_recipe_id,
                engine,
                text,
                searchable_text,
                confidence,
                language_hint,
                json.dumps(lines, ensure_ascii=False, sort_keys=True),
                json.dumps(raw_boxes, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE ocr_result
            SET engine = ?,
                text = ?,
                searchable_text = ?,
                confidence = ?,
                language_hint = ?,
                line_json = ?,
                bbox_json = ?,
                created_at = ?
            WHERE id = ?
            """,
            (
                engine,
                text,
                searchable_text,
                confidence,
                language_hint,
                json.dumps(lines, ensure_ascii=False, sort_keys=True),
                json.dumps(raw_boxes, ensure_ascii=False, sort_keys=True),
                now,
                result_id,
            ),
        )

    conn.execute("DELETE FROM ocr_result_fts WHERE result_id = ?", (result_id,))
    if searchable_text.strip():
        conn.execute(
            """
            INSERT INTO ocr_result_fts (result_id, asset_id, searchable_text)
            VALUES (?, ?, ?)
            """,
            (result_id, asset_id, searchable_text),
        )
    conn.execute(
        """
        UPDATE asset
        SET updated_at = ?
        WHERE id = ?
        """,
        (now, asset_id),
    )
    return result_id


def run_ocr_asset_job(
    conn: sqlite3.Connection,
    library_root_path: Path,
    payload: dict[str, object],
    backend: OcrBackend,
) -> None:
    asset_id = str(payload["asset_id"])
    ocr_recipe_id = str(payload["ocr_recipe_id"])
    media_type = str(payload.get("media_type") or "")
    if media_type == GIF_MEDIA_TYPE:
        return

    asset_row = conn.execute(
        """
        SELECT library_path, media_type
        FROM asset
        WHERE id = ?
          AND deleted_at IS NULL
        """,
        (asset_id,),
    ).fetchone()
    if asset_row is None:
        raise ValueError(f"Asset not found for OCR job: {asset_id}")
    if str(asset_row["media_type"]) == GIF_MEDIA_TYPE:
        return

    existing = conn.execute(
        """
        SELECT id
        FROM ocr_result
        WHERE asset_id = ?
          AND ocr_recipe_id = ?
        LIMIT 1
        """,
        (asset_id, ocr_recipe_id),
    ).fetchone()
    if existing is not None:
        return

    image_path = library_root_path / str(asset_row["library_path"])
    ocr_output = backend.recognize_image(image_path)
    store_ocr_result(conn, asset_id, ocr_recipe_id, ocr_output)


def delete_asset_ocr_results(conn: sqlite3.Connection, asset_id: str) -> None:
    conn.execute("DELETE FROM ocr_result_fts WHERE asset_id = ?", (asset_id,))


def collect_asset_ocr_rows(conn: sqlite3.Connection, asset_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT ocr.id, ocr.engine, ocr.text, ocr.searchable_text, ocr.confidence, ocr.language_hint,
               ocr.line_json, ocr.bbox_json, ocr.created_at,
               recipe.engine_version, recipe.model_key, recipe.preprocess_version, recipe.min_confidence
        FROM ocr_result ocr
        JOIN ocr_recipe recipe ON recipe.id = ocr.ocr_recipe_id
        WHERE ocr.asset_id = ?
        ORDER BY ocr.created_at DESC, ocr.id DESC
        """,
        (asset_id,),
    ).fetchall()


def collect_ocr_rows_for_assets(
    conn: sqlite3.Connection,
    asset_ids: list[str],
) -> list[sqlite3.Row]:
    if not asset_ids:
        return []
    placeholders = ", ".join("?" for _ in asset_ids)
    return conn.execute(
        f"""
        SELECT ocr.asset_id, ocr.id, ocr.engine, ocr.text, ocr.searchable_text, ocr.confidence, ocr.language_hint,
               ocr.line_json, ocr.bbox_json, ocr.created_at,
               recipe.engine_version, recipe.model_key, recipe.preprocess_version, recipe.min_confidence
        FROM ocr_result ocr
        JOIN ocr_recipe recipe ON recipe.id = ocr.ocr_recipe_id
        WHERE ocr.asset_id IN ({placeholders})
        ORDER BY ocr.created_at DESC, ocr.id DESC
        """,
        tuple(asset_ids),
    ).fetchall()


def project_asset_ocr_results(rows: list[sqlite3.Row]) -> list[dict[str, object]]:
    return [
        {
            "result_id": str(row["id"]),
            "engine": str(row["engine"]),
            "engine_version": str(row["engine_version"]),
            "model_key": str(row["model_key"]),
            "text": str(row["text"]),
            "searchable_text": str(row["searchable_text"]),
            "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
            "language_hint": str(row["language_hint"]) if row["language_hint"] else None,
            "line_json": json.loads(str(row["line_json"])),
            "bbox_json": json.loads(str(row["bbox_json"])),
            "preprocess_version": str(row["preprocess_version"]),
            "min_confidence": float(row["min_confidence"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def collect_ocr_search_rows(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[sqlite3.Row]:
    stripped_query = query.strip()
    if not stripped_query or limit <= 0:
        return []

    compact_query = "".join(stripped_query.split())
    if len(compact_query) >= 3:
        escaped_query = f'"{stripped_query.replace(chr(34), chr(34) + chr(34))}"'
        return conn.execute(
            """
            SELECT ocr.asset_id, ocr.text, ocr.searchable_text, ocr.confidence,
                   ocr.language_hint, ocr.created_at,
                   a.library_path, a.media_type, a.content_hash, a.width, a.height,
                   bm25(ocr_result_fts) AS ocr_rank
            FROM ocr_result_fts
            JOIN ocr_result ocr ON ocr.id = ocr_result_fts.result_id
            JOIN asset a ON a.id = ocr.asset_id
            WHERE ocr_result_fts MATCH ?
              AND a.deleted_at IS NULL
            ORDER BY ocr_rank ASC
            LIMIT ?
            """,
            (escaped_query, limit),
        ).fetchall()

    like_query = f"%{stripped_query}%"
    return conn.execute(
        """
        SELECT ocr.asset_id, ocr.text, ocr.searchable_text, ocr.confidence,
               ocr.language_hint, ocr.created_at,
               a.library_path, a.media_type, a.content_hash, a.width, a.height,
               0.0 AS ocr_rank
        FROM ocr_result ocr
        JOIN asset a ON a.id = ocr.asset_id
        WHERE ocr.searchable_text LIKE ?
          AND a.deleted_at IS NULL
        ORDER BY ocr.created_at DESC, ocr.id DESC
        LIMIT ?
        """,
        (like_query, limit),
    ).fetchall()


def ocr_snippet(query: str, text: str) -> str:
    stripped_query = query.strip().lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if stripped_query:
        for line in lines:
            if stripped_query in line.lower():
                return line
    return lines[0]


def project_ocr_search_rows(
    query: str,
    rows: list[sqlite3.Row],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for row in rows:
        row_keys = row.keys()
        text = str(row["text"])
        asset_id = str(row["asset_id"])
        results.append(
            {
                "asset_id": asset_id,
                "score": 0.0,
                "library_path": str(row["library_path"]),
                "library_url": f"/media/{str(row['library_path'])}",
                "thumbnail_url": f"/media/thumbnails/{asset_id}.jpg",
                "media_type": str(row["media_type"]),
                "content_hash": str(row["content_hash"]),
                "width": int(row["width"]) if "width" in row_keys and row["width"] is not None else None,
                "height": int(row["height"]) if "height" in row_keys and row["height"] is not None else None,
                "ocr_score": float(row["ocr_rank"]) if "ocr_rank" in row_keys and row["ocr_rank"] is not None else None,
                "ocr_confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "ocr_snippet": ocr_snippet(query, text),
                "ocr_text": text,
            }
        )
    return results
