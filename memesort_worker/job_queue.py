from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class JobType(str, Enum):
    GENERATE_THUMBNAIL = "generate_thumbnail"
    EMBED_ASSET = "embed_asset"
    OCR_ASSET = "ocr_asset"


RETRYABLE_JOB_TYPES = tuple(job_type.value for job_type in JobType)


@dataclass(frozen=True)
class PendingJob:
    job_id: str
    job_type: JobType | str
    asset_id: str | None
    recipe_id: str | None
    payload_json: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PendingJob":
        raw_job_type = str(row["type"])
        try:
            job_type: JobType | str = JobType(raw_job_type)
        except ValueError:
            job_type = raw_job_type
        return cls(
            job_id=str(row["id"]),
            job_type=job_type,
            asset_id=str(row["asset_id"]) if row["asset_id"] else None,
            recipe_id=str(row["recipe_id"]) if row["recipe_id"] else None,
            payload_json=str(row["payload_json"]),
        )

    @property
    def payload(self) -> dict[str, object]:
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError(f"Job {self.job_id} payload must be an object")
        return payload


class JobQueue:
    """Own the durable lifecycle of pending Indexing jobs."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def prepare(
        self,
        max_jobs: int | None,
    ) -> tuple[int, int, list[PendingJob]]:
        with self._conn:
            requeued_running, retried_failed = requeue_incomplete_jobs(self._conn)
        return (
            requeued_running,
            retried_failed,
            fetch_pending_jobs(self._conn, max_jobs=max_jobs),
        )

    def claim(self, job: PendingJob) -> bool:
        with self._conn:
            return mark_job_running(self._conn, job.job_id)

    def complete(self, job: PendingJob) -> None:
        with self._conn:
            mark_job_completed(self._conn, job.job_id)

    def fail(self, job: PendingJob, error: Exception) -> None:
        with self._conn:
            mark_job_failed(
                self._conn,
                job.job_id,
                error_code=type(error).__name__,
                error_detail=str(error),
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS job (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            asset_id TEXT REFERENCES asset(id) ON DELETE CASCADE,
            recipe_id TEXT REFERENCES embedding_recipe(id) ON DELETE CASCADE,
            payload_json TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_job_status_type
        ON job (status, type);
        """
    )


def _insert_job(
    conn: sqlite3.Connection,
    job_type: str,
    asset_id: str | None,
    recipe_id: str | None,
    payload: dict[str, object],
    now: str,
) -> int:
    normalized_job_type = JobType(job_type).value
    conn.execute(
        """
        INSERT INTO job (
            id,
            type,
            status,
            asset_id,
            recipe_id,
            payload_json,
            progress,
            attempt_count,
            error_code,
            error_detail,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            normalized_job_type,
            "pending",
            asset_id,
            recipe_id,
            json.dumps(payload, sort_keys=True),
            0.0,
            0,
            None,
            None,
            now,
            now,
        ),
    )
    return 1


def enqueue_thumbnail(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    now: str,
) -> int:
    return _insert_job(
        conn,
        JobType.GENERATE_THUMBNAIL.value,
        asset_id,
        None,
        {"asset_id": asset_id},
        now,
    )


def enqueue_embedding(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    recipe_id: str,
    media_type: str,
    now: str,
) -> int:
    return _insert_job(
        conn,
        JobType.EMBED_ASSET.value,
        asset_id,
        recipe_id,
        {
            "asset_id": asset_id,
            "recipe_id": recipe_id,
            "media_type": media_type,
        },
        now,
    )


def enqueue_ocr(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    ocr_recipe_id: str,
    media_type: str,
    now: str,
) -> int:
    return _insert_job(
        conn,
        JobType.OCR_ASSET.value,
        asset_id,
        None,
        {
            "asset_id": asset_id,
            "ocr_recipe_id": ocr_recipe_id,
            "media_type": media_type,
        },
        now,
    )


def fetch_pending_jobs(
    conn: sqlite3.Connection,
    max_jobs: int | None = None,
) -> list[PendingJob]:
    rows = conn.execute(
        """
        SELECT id, type, asset_id, recipe_id, payload_json
        FROM job
        WHERE status = 'pending'
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    selected_rows = rows if max_jobs is None else rows[:max_jobs]
    return [PendingJob.from_row(row) for row in selected_rows]


def requeue_incomplete_jobs(conn: sqlite3.Connection) -> tuple[int, int]:
    now = _utc_now()
    running_count = conn.execute(
        """
        UPDATE job
        SET status = 'pending',
            updated_at = ?
        WHERE status = 'running'
        """,
        (now,),
    ).rowcount
    failed_count = conn.execute(
        f"""
        UPDATE job
        SET status = 'pending',
            updated_at = ?
        WHERE status = 'failed'
          AND type IN ({_retryable_job_placeholders()})
        """,
        (now, *RETRYABLE_JOB_TYPES),
    ).rowcount
    return int(running_count), int(failed_count)


def retry_failed_jobs(conn: sqlite3.Connection) -> tuple[int, int]:
    retried_jobs = conn.execute(
        f"""
        UPDATE job
        SET status = 'pending',
            updated_at = ?,
            error_code = NULL,
            error_detail = NULL
        WHERE status = 'failed'
          AND type IN ({_retryable_job_placeholders()})
        """,
        (_utc_now(), *RETRYABLE_JOB_TYPES),
    ).rowcount
    failed_jobs_remaining = int(
        conn.execute(
            "SELECT COUNT(*) FROM job WHERE status = 'failed'",
        ).fetchone()[0]
    )
    return int(retried_jobs), failed_jobs_remaining


def mark_job_running(conn: sqlite3.Connection, job_id: str) -> bool:
    """Claim a pending job without reviving a job deleted by an operator."""
    updated = conn.execute(
        """
        UPDATE job
        SET status = 'running',
            updated_at = ?,
            attempt_count = attempt_count + 1,
            error_code = NULL,
            error_detail = NULL
        WHERE id = ?
          AND status = 'pending'
        """,
        (_utc_now(), job_id),
    ).rowcount
    return bool(updated)


def mark_job_completed(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        """
        UPDATE job
        SET status = 'completed',
            progress = 1.0,
            updated_at = ?
        WHERE id = ?
        """,
        (_utc_now(), job_id),
    )


def mark_job_failed(
    conn: sqlite3.Connection,
    job_id: str,
    error_code: str,
    error_detail: str,
) -> None:
    conn.execute(
        """
        UPDATE job
        SET status = 'failed',
            updated_at = ?,
            error_code = ?,
            error_detail = ?
        WHERE id = ?
        """,
        (_utc_now(), error_code, error_detail[:1000], job_id),
    )


def count_asset_jobs(conn: sqlite3.Connection, asset_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM job WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()[0]
    )


def has_incomplete_job(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    job_type: str,
    recipe_id: str | None = None,
) -> bool:
    query = """
        SELECT 1
        FROM job
        WHERE asset_id = ?
          AND type = ?
          AND status IN ('pending', 'running', 'failed')
    """
    params: list[object] = [asset_id, job_type]
    if recipe_id is not None:
        query += "\n          AND recipe_id = ?"
        params.append(recipe_id)
    query += "\n        LIMIT 1"
    return conn.execute(query, tuple(params)).fetchone() is not None


def collect_asset_job_rows(conn: sqlite3.Connection, asset_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, type, status, recipe_id, attempt_count
        FROM job
        WHERE asset_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (asset_id,),
    ).fetchall()


def collect_status_job_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, type, status, asset_id, recipe_id, attempt_count, created_at, updated_at, error_code, error_detail
        FROM job
        ORDER BY updated_at DESC, created_at DESC, id DESC
        """
    ).fetchall()


def count_jobs_by_status(job_rows: list[sqlite3.Row]) -> dict[str, int]:
    job_counts: dict[str, int] = {}
    for job_row in job_rows:
        status = str(job_row["status"])
        job_counts[status] = job_counts.get(status, 0) + 1
    return job_counts


def project_recent_jobs(job_rows: list[sqlite3.Row], limit: int = 20) -> list[dict[str, object]]:
    return [
        {
            "job_id": str(job_row["id"]),
            "type": str(job_row["type"]),
            "status": str(job_row["status"]),
            "asset_id": str(job_row["asset_id"]) if job_row["asset_id"] else None,
            "recipe_id": str(job_row["recipe_id"]) if job_row["recipe_id"] else None,
            "attempt_count": int(job_row["attempt_count"]),
            "created_at": str(job_row["created_at"]),
            "updated_at": str(job_row["updated_at"]),
            "error_code": str(job_row["error_code"]) if job_row["error_code"] else None,
            "error_detail": str(job_row["error_detail"]) if job_row["error_detail"] else None,
        }
        for job_row in job_rows[:limit]
    ]


def _retryable_job_placeholders() -> str:
    return ", ".join("?" for _ in RETRYABLE_JOB_TYPES)
