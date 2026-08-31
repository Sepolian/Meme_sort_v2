"""Characterization tests for public read projections.

These tests pin the payloads produced by the read interfaces the web
routes call today, across the six documented asset states: no assets,
pending initial index, indexed, failed, stale-only, and reindex-pending.
The oracle is a set of frozen expected fixtures plus raw SQL facts, so
the projections are verified independently of the projection code.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from memesort_worker import asset_catalog
from memesort_worker import runtime_service
from memesort_worker.app_commands import import_and_start_indexing
from memesort_worker.indexing_pipeline import run_pending_jobs
from memesort_worker.library import (
    get_asset_detail,
    get_library_status,
    import_folder,
    initialize_library,
    list_assets,
    scan_duplicate_assets,
)
from memesort_worker.library_store import LibraryStore
from memesort_worker.runtime_manifest import load_runtime_manifest
from runtime_fakes import FakeIndexingRuntime


ASSET_STATES = (
    "no_assets",
    "pending_initial_index",
    "indexed",
    "failed",
    "stale_only",
    "reindex_pending",
)

EXPECTED_STATUS_BY_STATE = {
    "no_assets": None,
    "pending_initial_index": "pending_initial_index",
    "indexed": "indexed",
    "failed": "failed",
    "stale_only": "stale_only",
    "reindex_pending": "reindex_pending",
}

FULL_ASSET_PROJECTION_KEYS = {
    "asset_id",
    "library_path",
    "library_url",
    "thumbnail_url",
    "media_type",
    "content_hash",
    "width",
    "height",
    "imported_at",
    "updated_at",
    "source_record_count",
    "source_records",
    "indexed_recipe_labels",
    "stale_recipe_labels",
    "status",
    "ocr_status",
    "ocr_results",
    "renditions",
    "jobs",
}

SUMMARY_PROJECTION_KEYS = {
    "asset_id",
    "library_path",
    "library_url",
    "thumbnail_url",
    "media_type",
    "content_hash",
    "width",
    "height",
    "imported_at",
    "updated_at",
    "source_record_count",
    "source_records",
    "status",
}

# Fields whose values must agree between the full and the summary projection.
SHARED_PROJECTION_KEYS = SUMMARY_PROJECTION_KEYS - {"source_records"}

PENDING_JOB_KEYS = {
    "job_id",
    "type",
    "asset_id",
    "asset_path",
    "recipe_id",
    "attempt_count",
    "created_at",
    "updated_at",
}

LIBRARY_STATUS_KEYS = {
    "library_root",
    "active_recipe_id",
    "active_recipe_label",
    "asset_counts",
    "job_counts",
    "total_assets",
    "total_jobs",
    "recent_jobs",
}

SETUP_STATE_KEYS = {
    "library_root",
    "health_check_has_run",
    "health_check_ok",
    "health_check_summary",
    "import_source_hint",
    "assets_present",
    "indexed_assets_present",
    "pending_assets_present",
    "active_recipe_label",
    "runtime_readiness",
    "checklist",
}

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)")

# Expected (indexed_recipe_labels, stale_recipe_labels) per asset state.
EXPECTED_RECIPE_LABELS_BY_STATE = {
    "pending_initial_index": ([], []),
    "indexed": (["{recipe_label}"], []),
    "failed": ([], []),
    "stale_only": (["{recipe_label}"], ["{recipe_label}"]),
    "reindex_pending": (["{recipe_label}"], ["{recipe_label}"]),
}


def _job_sort_key(job: dict[str, object]) -> tuple[str, str, str]:
    return (str(job["type"]), str(job["status"]), str(job["job_id"]))


def _expected_job(
    job_type: str,
    status: str,
    attempt_count: int,
    job_id: str = "{uuid}",
    recipe_id: str | None = None,
) -> dict[str, object]:
    return {
        "job_id": job_id,
        "type": job_type,
        "status": status,
        "asset_id": "{asset}",
        "recipe_id": recipe_id,
        "attempt_count": attempt_count,
        "created_at": "{ts}",
        "updated_at": "{ts}",
        "error_code": None,
        "error_detail": None,
    }


def _expected_library_status(
    asset_counts: dict[str, int],
    job_counts: dict[str, int],
    total_assets: int,
    total_jobs: int,
    recent_jobs: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "library_root": "{library_root}",
        "active_recipe_id": "{recipe}",
        "active_recipe_label": "{recipe_label}",
        "asset_counts": asset_counts,
        "job_counts": job_counts,
        "total_assets": total_assets,
        "total_jobs": total_jobs,
        "recent_jobs": sorted(recent_jobs, key=_job_sort_key),
    }


EXPECTED_LIBRARY_STATUS_BY_STATE = {
    "no_assets": _expected_library_status({}, {}, 0, 0, []),
    "pending_initial_index": _expected_library_status(
        {"pending_initial_index": 1},
        {"pending": 3},
        1,
        3,
        [
            _expected_job("embed_asset", "pending", 0, recipe_id="{recipe}"),
            _expected_job("generate_thumbnail", "pending", 0),
            _expected_job("ocr_asset", "pending", 0),
        ],
    ),
    "indexed": _expected_library_status(
        {"indexed": 1},
        {"completed": 3},
        1,
        3,
        [
            _expected_job("embed_asset", "completed", 1, recipe_id="{recipe}"),
            _expected_job("generate_thumbnail", "completed", 1),
            _expected_job("ocr_asset", "completed", 1),
        ],
    ),
    "failed": _expected_library_status(
        {"failed": 1},
        {"failed": 1, "pending": 2},
        1,
        3,
        [
            _expected_job("embed_asset", "failed", 0, recipe_id="{recipe}"),
            _expected_job("generate_thumbnail", "pending", 0),
            _expected_job("ocr_asset", "pending", 0),
        ],
    ),
    "stale_only": _expected_library_status(
        {"stale_only": 1},
        {"completed": 3},
        1,
        3,
        [
            _expected_job("embed_asset", "completed", 1, recipe_id="{recipe}"),
            _expected_job("generate_thumbnail", "completed", 1),
            _expected_job("ocr_asset", "completed", 1),
        ],
    ),
    "reindex_pending": _expected_library_status(
        {"reindex_pending": 1},
        {"completed": 3, "pending": 1},
        1,
        4,
        [
            _expected_job("embed_asset", "completed", 1, recipe_id="{recipe}"),
            _expected_job(
                "embed_asset", "pending", 0, job_id="reindex-job", recipe_id="{recipe}"
            ),
            _expected_job("generate_thumbnail", "completed", 1),
            _expected_job("ocr_asset", "completed", 1),
        ],
    ),
}


class StubEmbeddingBackend:
    backend_id = "llama.cpp-vulkan::characterization"

    def __init__(self) -> None:
        manifest = load_runtime_manifest()
        vector = np.ones(manifest.model.output_dimension, dtype=np.float32)
        self.vector = vector / np.linalg.norm(vector)

    def embed_text(self, text, output_dimension, instruction=None) -> np.ndarray:
        del text, instruction, output_dimension
        return self.vector

    def embed_image_bytes(self, image_bytes, output_dimension, instruction=None) -> np.ndarray:
        del image_bytes, instruction, output_dimension
        return self.vector


class StubOcrBackend:
    backend_id = "stub-ocr"

    def recognize_image(self, image_path: Path) -> dict[str, object]:
        return {
            "engine": self.backend_id,
            "text": image_path.stem,
            "texts": [image_path.stem],
            "scores": [1.0],
            "boxes": [[]],
            "language_hint": "test",
        }

    def close(self) -> None:
        return


class RecordingWorkerLoop:
    def __init__(self) -> None:
        self.resume_calls = 0

    def resume(self) -> None:
        self.resume_calls += 1

    def snapshot(self):
        loop = self

        class _Snapshot:
            @staticmethod
            def to_dict() -> dict[str, object]:
                return {"state": "running", "resume_calls": loop.resume_calls}

        return _Snapshot()


class StubRuntimeGate:
    def __init__(self, ready: bool, message: str) -> None:
        self._verdict = (ready, message)

    def is_ready_for_indexing(self) -> tuple[bool, str]:
        return self._verdict

    def current_health_check(self):
        return None


class ReadProjectionCharacterizationTests(unittest.TestCase):
    def _write_image(self, path: Path, color: tuple[int, int, int] = (255, 0, 0)) -> None:
        Image.new("RGB", (40, 30), color).save(path, format="PNG")

    def _import_one_image(self, root: Path) -> Path:
        library_root = root / "library"
        source_root = root / "source"
        source_root.mkdir(parents=True, exist_ok=True)
        self._write_image(source_root / "reaction.png")
        import_folder(library_root, source_root)
        return library_root

    def _run_all_jobs_with_stubs(self, library_root: Path, expected_completed: int = 3) -> None:
        runtime = FakeIndexingRuntime(
            embedding_backend=StubEmbeddingBackend(),
            ocr_backend=StubOcrBackend(),
        )
        result = run_pending_jobs(library_root, runtime)
        assert result.failed_jobs == 0, result
        assert result.completed_jobs == expected_completed, result

    def _connect(self, library_root: Path) -> sqlite3.Connection:
        return asset_catalog.connect(asset_catalog.database_path(library_root))

    def _make_embeddings_stale(self, library_root: Path) -> None:
        conn = self._connect(library_root)
        try:
            active_recipe_id = asset_catalog.get_active_recipe_id(conn)
            with conn:
                conn.execute(
                    """
                    INSERT INTO embedding_recipe (
                        id, family_key, model_id, model_revision, output_dimension,
                        runtime_profile, preprocess_version, instruction_key,
                        pooling_key, normalized, gif_frame_count, created_at
                    )
                    SELECT 'stale-recipe', family_key, model_id, 'stale-revision',
                           output_dimension, runtime_profile, preprocess_version,
                           instruction_key, pooling_key, normalized, gif_frame_count,
                           created_at
                    FROM embedding_recipe WHERE id = ?
                    """,
                    (active_recipe_id,),
                )
                conn.execute("UPDATE embedding_item SET recipe_id = 'stale-recipe'")
        finally:
            conn.close()

    def _enqueue_active_embed_job(self, library_root: Path) -> None:
        conn = self._connect(library_root)
        try:
            active_recipe_id = asset_catalog.get_active_recipe_id(conn)
            asset_id = conn.execute("SELECT id FROM asset").fetchone()["id"]
            with conn:
                conn.execute(
                    """
                    INSERT INTO job (
                        id, type, status, asset_id, recipe_id, payload_json,
                        progress, attempt_count, created_at, updated_at
                    )
                    VALUES ('reindex-job', 'embed_asset', 'pending', ?, ?, '{}',
                            0, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                    """,
                    (asset_id, active_recipe_id),
                )
        finally:
            conn.close()

    def _build_state(self, root: Path, state: str) -> Path:
        if state == "no_assets":
            library_root = root / "library"
            initialize_library(library_root)
            return library_root
        library_root = self._import_one_image(root)
        if state == "pending_initial_index":
            return library_root
        if state == "failed":
            conn = self._connect(library_root)
            try:
                with conn:
                    conn.execute("UPDATE job SET status = 'failed' WHERE type = 'embed_asset'")
            finally:
                conn.close()
            return library_root
        self._run_all_jobs_with_stubs(library_root)
        if state == "indexed":
            return library_root
        self._make_embeddings_stale(library_root)
        if state == "stale_only":
            return library_root
        if state == "reindex_pending":
            self._enqueue_active_embed_job(library_root)
            return library_root
        raise AssertionError(f"unknown state {state}")

    def _oracle_literals(self, root: Path, library_root: Path) -> list[tuple[str, str]]:
        """Collect replacement literals from raw SQL, independent of the projections."""
        conn = self._connect(library_root)
        try:
            asset_ids = [
                str(row["id"]) for row in conn.execute("SELECT id FROM asset")
            ]
            recipe_id = asset_catalog.get_active_recipe_id(conn)
            recipe = conn.execute(
                "SELECT model_id, output_dimension, runtime_profile "
                "FROM embedding_recipe WHERE id = ?",
                (recipe_id,),
            ).fetchone()
        finally:
            conn.close()
        label = (
            f"{str(recipe['model_id']).split('/')[-1]} / "
            f"{int(recipe['output_dimension'])}d / {recipe['runtime_profile']}"
        )
        literals = [(label, "{recipe_label}"), (str(recipe_id), "{recipe}")]
        literals.extend((asset_id, "{asset}") for asset_id in asset_ids)
        literals.append((str(library_root.resolve()), "{library_root}"))
        literals.append((str(root / "source" / "reaction.png"), "{source_png}"))
        return literals

    def _freeze(self, payload, literals: list[tuple[str, str]]):
        if isinstance(payload, dict):
            return {key: self._freeze(value, literals) for key, value in payload.items()}
        if isinstance(payload, list):
            return [self._freeze(value, literals) for value in payload]
        if isinstance(payload, str):
            for literal, token in literals:
                payload = payload.replace(literal, token)
            payload = _TIMESTAMP_RE.sub("{ts}", payload)
            return _UUID_RE.sub("{uuid}", payload)
        return payload

    def _expected_summary(self, root: Path, state: str) -> dict[str, object]:
        has_thumbnail = state in {"indexed", "stale_only", "reindex_pending"}
        content_hash = hashlib.sha256(
            (root / "source" / "reaction.png").read_bytes()
        ).hexdigest()
        return {
            "asset_id": "{asset}",
            "library_path": "originals/{asset}.png",
            "library_url": "/media/originals/{asset}.png",
            "thumbnail_url": (
                "/media/thumbnails/{asset}.jpg" if has_thumbnail else None
            ),
            "media_type": "image/png",
            "content_hash": content_hash,
            "width": 40,
            "height": 30,
            "imported_at": "{ts}",
            "updated_at": "{ts}",
            "source_record_count": 1,
            "source_records": [{"source_path": "{source_png}"}],
            "status": EXPECTED_STATUS_BY_STATE[state],
        }

    def test_status_matrix_old_and_new_projections_agree(self) -> None:
        for state in ASSET_STATES:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                library_root = self._build_state(root, state)
                literals = self._oracle_literals(root, library_root)
                old_assets = self._freeze(list_assets(library_root).assets, literals)
                with LibraryStore(library_root) as store:
                    new_assets = self._freeze(
                        store.list_asset_summaries().assets, literals
                    )

                if EXPECTED_STATUS_BY_STATE[state] is None:
                    self.assertEqual([], old_assets)
                    self.assertEqual([], new_assets)
                    continue

                expected_summary = self._expected_summary(root, state)
                self.assertEqual([expected_summary], new_assets, state)
                self.assertEqual(1, len(old_assets))
                full = old_assets[0]
                self.assertEqual(FULL_ASSET_PROJECTION_KEYS, set(full))
                for key in SHARED_PROJECTION_KEYS:
                    self.assertEqual(
                        expected_summary[key], full[key], f"{state}:{key}"
                    )
                indexed_labels, stale_labels = EXPECTED_RECIPE_LABELS_BY_STATE[state]
                self.assertEqual(indexed_labels, full["indexed_recipe_labels"], state)
                self.assertEqual(stale_labels, full["stale_recipe_labels"], state)

    def test_library_status_payloads_agree_across_implementations(self) -> None:
        for state in ASSET_STATES:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                library_root = self._build_state(root, state)
                literals = self._oracle_literals(root, library_root)
                old_status = get_library_status(library_root).to_dict()
                with LibraryStore(library_root) as store:
                    new_status = store.get_library_status().to_dict()

                expected = EXPECTED_LIBRARY_STATUS_BY_STATE[state]
                for payload in (old_status, new_status):
                    frozen = self._freeze(payload, literals)
                    self.assertEqual(LIBRARY_STATUS_KEYS, set(frozen))
                    frozen["recent_jobs"] = sorted(
                        frozen["recent_jobs"], key=_job_sort_key
                    )
                    self.assertEqual(expected, frozen, state)

    def _expected_asset_detail(self, root: Path) -> dict[str, object]:
        content_hash = hashlib.sha256(
            (root / "source" / "reaction.png").read_bytes()
        ).hexdigest()
        return {
            "library_root": "{library_root}",
            "active_recipe_id": "{recipe}",
            "active_recipe_label": "{recipe_label}",
            "asset": {
                "asset_id": "{asset}",
                "library_path": "originals/{asset}.png",
                "library_url": "/media/originals/{asset}.png",
                "thumbnail_url": "/media/thumbnails/{asset}.jpg",
                "media_type": "image/png",
                "content_hash": content_hash,
                "width": 40,
                "height": 30,
                "imported_at": "{ts}",
                "updated_at": "{ts}",
                "source_record_count": 1,
                "source_records": [
                    {
                        "source_path": "{source_png}",
                        "imported_at": "{ts}",
                        "last_seen_at": "{ts}",
                    }
                ],
                "indexed_recipe_labels": ["{recipe_label}"],
                "stale_recipe_labels": [],
                "status": "indexed",
                "ocr_status": "ready",
                "ocr_results": [
                    {
                        "result_id": "{uuid}",
                        "engine": "stub-ocr",
                        "engine_version": "3.6.0",
                        "model_key": "PP-OCRv5",
                        "text": "{asset}",
                        "searchable_text": "{asset}",
                        "confidence": 1.0,
                        "language_hint": "test",
                        "line_json": [
                            {"bbox": [], "confidence": 1.0, "text": "{asset}"}
                        ],
                        "bbox_json": [[]],
                        "preprocess_version": "original-still-v1",
                        "min_confidence": 0.5,
                        "created_at": "{ts}",
                    }
                ],
                "renditions": [
                    {
                        "kind": "thumbnail",
                        "path": "thumbnails/{asset}.jpg",
                        "url": "/media/thumbnails/{asset}.jpg",
                        "width": 40,
                        "height": 30,
                        "frame_index": None,
                        "created_at": "{ts}",
                    }
                ],
                "jobs": [
                    {
                        "job_id": "{uuid}",
                        "type": "embed_asset",
                        "status": "completed",
                        "recipe_id": "{recipe}",
                        "attempt_count": 1,
                    },
                    {
                        "job_id": "{uuid}",
                        "type": "generate_thumbnail",
                        "status": "completed",
                        "recipe_id": None,
                        "attempt_count": 1,
                    },
                    {
                        "job_id": "{uuid}",
                        "type": "ocr_asset",
                        "status": "completed",
                        "recipe_id": None,
                        "attempt_count": 1,
                    },
                ],
            },
        }

    def test_asset_detail_payload_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = self._build_state(root, "indexed")
            literals = self._oracle_literals(root, library_root)
            conn = self._connect(library_root)
            try:
                asset_id = str(conn.execute("SELECT id FROM asset").fetchone()["id"])
            finally:
                conn.close()

            detail = get_asset_detail(library_root, asset_id=asset_id)
            frozen = self._freeze(detail.to_dict(), literals)
            frozen["asset"]["jobs"] = sorted(
                frozen["asset"]["jobs"], key=_job_sort_key
            )
            self.assertEqual(self._expected_asset_detail(root), frozen)

    def test_asset_detail_unknown_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._build_state(Path(temp_dir), "no_assets")
            with self.assertRaisesRegex(ValueError, "Unknown asset id"):
                get_asset_detail(library_root, asset_id="missing")

    def test_pending_jobs_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._build_state(Path(temp_dir), "pending_initial_index")
            with LibraryStore(library_root) as store:
                jobs = store.list_pending_jobs()

        self.assertEqual(3, len(jobs))
        self.assertEqual(
            {"generate_thumbnail", "embed_asset", "ocr_asset"},
            {job["type"] for job in jobs},
        )
        for job in jobs:
            self.assertEqual(PENDING_JOB_KEYS, set(job))
            self.assertEqual(0, job["attempt_count"])

    def test_duplicate_scan_payload_and_threshold_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_image(source_root / "first.png", (255, 0, 0))
            self._write_image(source_root / "second.png", (0, 255, 0))
            import_folder(library_root, source_root)
            self._run_all_jobs_with_stubs(library_root, expected_completed=6)

            for bad_threshold in (-0.1, 1.5):
                with self.assertRaisesRegex(ValueError, "threshold"):
                    scan_duplicate_assets(library_root, threshold=bad_threshold)

            result = scan_duplicate_assets(library_root, threshold=0.9)
            payload = result.to_dict()

        self.assertEqual(
            {"library_root", "active_recipe_id", "active_recipe_label", "threshold", "pairs"},
            set(payload),
        )
        self.assertEqual(0.9, payload["threshold"])
        self.assertEqual(1, len(payload["pairs"]))
        pair = payload["pairs"][0]
        self.assertGreaterEqual(float(pair["score"]), 0.9)
        self.assertNotEqual(pair["asset_a_id"], pair["asset_b_id"])

    def test_import_and_start_indexing_rejects_unready_runtime_before_importing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_image(source_root / "reaction.png")
            worker_loop = RecordingWorkerLoop()

            with self.assertRaisesRegex(ValueError, "runtime is not ready"):
                import_and_start_indexing(
                    library_root,
                    source_root,
                    worker_loop,
                    StubRuntimeGate(False, "runtime is not ready"),
                )

            self.assertEqual(0, worker_loop.resume_calls)
            self.assertEqual([], list_assets(library_root).assets)

    def test_import_and_start_indexing_response_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_image(source_root / "reaction.png")
            worker_loop = RecordingWorkerLoop()

            response = import_and_start_indexing(
                library_root,
                source_root,
                worker_loop,
                StubRuntimeGate(True, "ready"),
            )

        self.assertEqual({"import_result", "worker_loop"}, set(response))
        self.assertEqual(1, worker_loop.resume_calls)
        self.assertEqual(1, response["import_result"]["new_assets"])
        self.assertEqual(3, response["import_result"]["jobs_created"])
        self.assertEqual(
            {"state": "running", "resume_calls": 1}, response["worker_loop"]
        )

    def test_setup_state_payload_for_fresh_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._build_state(Path(temp_dir), "no_assets")
            state = runtime_service.get_setup_state(
                library_root,
                StubRuntimeGate(False, "runtime health has not been checked"),
            ).to_dict()

        self.assertEqual(SETUP_STATE_KEYS, set(state))
        self.assertFalse(state["health_check_has_run"])
        self.assertFalse(state["health_check_ok"])
        self.assertFalse(state["assets_present"])
        self.assertFalse(state["indexed_assets_present"])
        self.assertFalse(state["pending_assets_present"])
        self.assertIsNone(state["import_source_hint"])
        self.assertEqual(
            ["runtime-files", "health-check", "import-assets", "indexed-assets"],
            [item["id"] for item in state["checklist"]],
        )
        self.assertFalse(state["runtime_readiness"]["ready"])
        self.assertEqual(
            "Vulkan health has not been checked in this app session.",
            state["health_check_summary"],
        )

    def test_setup_state_reports_import_hint_and_pending_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = self._build_state(root, "pending_initial_index")
            state = runtime_service.get_setup_state(
                library_root,
                StubRuntimeGate(False, "runtime health has not been checked"),
            ).to_dict()

            self.assertTrue(state["assets_present"])
            self.assertTrue(state["pending_assets_present"])
            self.assertFalse(state["indexed_assets_present"])
            self.assertEqual(
                str(root / "source" / "reaction.png"), state["import_source_hint"]
            )


if __name__ == "__main__":
    unittest.main()
