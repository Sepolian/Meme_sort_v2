"""Characterization tests for public read projections.

These tests record the payloads produced by the read interfaces the web
routes call today, across the six documented asset states: no assets,
pending initial index, indexed, failed, stale-only, and reindex-pending.
They exist so later refactors can prove behaviour is preserved.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from memesort_worker import asset_catalog
from memesort_worker import runtime_service
from memesort_worker.asset_browse import (
    get_library_status as browse_get_library_status,
    list_pending_jobs,
)
from memesort_worker.app_commands import import_and_start_indexing
from memesort_worker.library import (
    get_asset_detail,
    get_library_status,
    import_folder,
    initialize_library,
    list_assets,
    run_pending_jobs,
    scan_duplicate_assets,
)
from memesort_worker.library_store import LibraryStore
from memesort_worker.runtime_manifest import load_runtime_manifest


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


class ReadProjectionCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_service._clear_current_health_checks()

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
        with patch(
            "memesort_worker.indexing_pipeline.get_embedding_backend",
            return_value=StubEmbeddingBackend(),
        ), patch(
            "memesort_worker.indexing_pipeline.is_runtime_ready_for_indexing",
            return_value=(True, "test health passed"),
        ), patch(
            "memesort_worker.indexing_pipeline.get_ocr_backend",
            return_value=StubOcrBackend(),
        ):
            result = run_pending_jobs(library_root)
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

    def test_status_matrix_old_and_new_projections_agree(self) -> None:
        for state in ASSET_STATES:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp_dir:
                library_root = self._build_state(Path(temp_dir), state)
                old_assets = list_assets(library_root).assets
                with LibraryStore(library_root) as store:
                    new_assets = store.list_asset_summaries().assets

                expected_status = EXPECTED_STATUS_BY_STATE[state]
                if expected_status is None:
                    self.assertEqual([], old_assets)
                    self.assertEqual([], new_assets)
                    continue

                self.assertEqual(1, len(old_assets))
                self.assertEqual(1, len(new_assets))
                self.assertEqual(expected_status, old_assets[0]["status"])
                self.assertEqual(expected_status, new_assets[0]["status"])
                self.assertEqual(FULL_ASSET_PROJECTION_KEYS, set(old_assets[0]))
                self.assertEqual(SUMMARY_PROJECTION_KEYS, set(new_assets[0]))
                for key in SHARED_PROJECTION_KEYS:
                    self.assertEqual(
                        old_assets[0][key], new_assets[0][key], f"{state}:{key}"
                    )

    def test_library_status_payloads_agree_across_implementations(self) -> None:
        for state in ASSET_STATES:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp_dir:
                library_root = self._build_state(Path(temp_dir), state)
                old_status = get_library_status(library_root).to_dict()
                new_status = browse_get_library_status(library_root).to_dict()
                self.assertEqual(LIBRARY_STATUS_KEYS, set(old_status))
                self.assertEqual(old_status, new_status, state)

    def test_asset_detail_matches_list_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._build_state(Path(temp_dir), "indexed")
            listed = list_assets(library_root)
            asset_id = str(listed.assets[0]["asset_id"])
            detail = get_asset_detail(library_root, asset_id=asset_id)

            payload = detail.to_dict()
            self.assertEqual(
                {"library_root", "active_recipe_id", "active_recipe_label", "asset"},
                set(payload),
            )
            self.assertEqual(listed.assets[0], detail.asset)
            self.assertEqual(listed.active_recipe_id, detail.active_recipe_id)
            self.assertEqual(listed.active_recipe_label, detail.active_recipe_label)
            self.assertEqual("indexed", detail.asset["status"])
            self.assertEqual("ready", detail.asset["ocr_status"])
            self.assertTrue(str(detail.asset["thumbnail_url"]).startswith("/media/"))
            self.assertTrue(str(detail.asset["library_url"]).startswith("/media/"))

    def test_asset_detail_unknown_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._build_state(Path(temp_dir), "no_assets")
            with self.assertRaisesRegex(ValueError, "Unknown asset id"):
                get_asset_detail(library_root, asset_id="missing")

    def test_pending_jobs_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._build_state(Path(temp_dir), "pending_initial_index")
            jobs = list_pending_jobs(library_root)

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

            with patch(
                "memesort_worker.app_commands.is_runtime_ready_for_indexing",
                return_value=(False, "runtime is not ready"),
            ):
                with self.assertRaisesRegex(ValueError, "runtime is not ready"):
                    import_and_start_indexing(library_root, source_root, worker_loop)

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

            with patch(
                "memesort_worker.app_commands.is_runtime_ready_for_indexing",
                return_value=(True, "ready"),
            ):
                response = import_and_start_indexing(library_root, source_root, worker_loop)

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
            state = runtime_service.get_setup_state(library_root).to_dict()

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
            state = runtime_service.get_setup_state(library_root).to_dict()

            self.assertTrue(state["assets_present"])
            self.assertTrue(state["pending_assets_present"])
            self.assertFalse(state["indexed_assets_present"])
            self.assertEqual(
                str(root / "source" / "reaction.png"), state["import_source_hint"]
            )


if __name__ == "__main__":
    unittest.main()
