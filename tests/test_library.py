from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from memesort_worker.app_state import build_app_state
from memesort_worker.cli import run
from memesort_worker.library import (
    DATABASE_NAME,
    delete_asset,
    delete_pending_jobs,
    get_library_status,
    import_folder,
    initialize_library,
    list_assets,
    remove_source_record,
    retry_failed_jobs,
    run_pending_jobs,
    search_text,
)
from memesort_worker.runtime_descriptor import get_runtime_descriptor
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.webapp import create_app


class StubEmbeddingBackend:
    backend_id = "llama.cpp-vulkan::test"

    def __init__(self) -> None:
        manifest = load_runtime_manifest()
        vector = np.ones(manifest.model.output_dimension, dtype=np.float32)
        self.vector = vector / np.linalg.norm(vector)

    def embed_text(
        self,
        text: str,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        del text, instruction
        assert output_dimension == self.vector.shape[0]
        return self.vector

    def embed_image_bytes(
        self,
        image_bytes: bytes,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        del image_bytes, instruction
        assert output_dimension == self.vector.shape[0]
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


class LibraryTests(unittest.TestCase):
    def _write_image(self, path: Path, color: tuple[int, int, int] = (255, 0, 0)) -> None:
        image = Image.new("RGB", (40, 30), color)
        image.save(path, format="PNG")

    def _import_one_image(self, root: Path) -> tuple[Path, Path]:
        library_root = root / "library"
        source_root = root / "source"
        source_root.mkdir()
        self._write_image(source_root / "reaction.png")
        import_folder(library_root, source_root)
        return library_root, source_root

    def _run_all_jobs_with_stubs(self, library_root: Path) -> StubEmbeddingBackend:
        backend = StubEmbeddingBackend()
        with patch(
            "memesort_worker.library.get_embedding_backend", return_value=backend
        ), patch(
            "memesort_worker.indexing_pipeline.get_ocr_backend",
            return_value=StubOcrBackend(),
        ):
            result = run_pending_jobs(library_root)

        self.assertEqual(0, result.failed_jobs)
        self.assertEqual(3, result.completed_jobs)
        self.assertEqual(backend.backend_id, result.backend)
        return backend

    def _request(
        self,
        app,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, object]]:
        body = json.dumps(payload or {}).encode("utf-8")
        captured: dict[str, str] = {}

        def start_response(status: str, _headers: object) -> None:
            captured["status"] = status

        response_body = b"".join(
            app(
                {
                    "REQUEST_METHOD": method,
                    "PATH_INFO": path,
                    "QUERY_STRING": "",
                    "CONTENT_LENGTH": str(len(body)),
                    "wsgi.input": BytesIO(body),
                },
                start_response,
            )
        )
        return captured["status"], json.loads(response_body.decode("utf-8"))

    def test_initialize_library_creates_manifest_recipe(self) -> None:
        manifest = load_runtime_manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            result = initialize_library(library_root)

            self.assertTrue((library_root / DATABASE_NAME).is_file())
            self.assertTrue(result.created_database)
            self.assertEqual(str(library_root.resolve()), result.library_root)
            for directory in (
                "originals",
                "thumbnails",
                "frames",
                "contact_sheets",
                "models",
                "runtime",
                "logs",
            ):
                self.assertTrue((library_root / directory).is_dir(), directory)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                row = conn.execute(
                    "SELECT model_id, output_dimension, runtime_profile FROM embedding_recipe"
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(
            (manifest.model.id, manifest.model.output_dimension, "vulkan"), row
        )

    def test_import_coalesces_duplicate_content_and_schedules_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            nested_root = source_root / "nested"
            nested_root.mkdir(parents=True)
            self._write_image(source_root / "same-a.png")
            (nested_root / "same-b.png").write_bytes((source_root / "same-a.png").read_bytes())
            (source_root / "ignore.txt").write_text("not an image", encoding="utf-8")

            first = import_folder(library_root, source_root)
            second = import_folder(library_root, source_root)
            assets = list_assets(library_root)

        self.assertEqual(3, first.discovered_files)
        self.assertEqual(2, first.supported_files)
        self.assertEqual(1, first.unsupported_files)
        self.assertEqual(1, first.new_assets)
        self.assertEqual(1, first.duplicate_assets)
        self.assertEqual(3, first.jobs_created)
        self.assertEqual(0, second.new_assets)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(2, assets.assets[0]["source_record_count"])
        self.assertEqual("pending_initial_index", assets.assets[0]["status"])

    def test_pending_jobs_create_thumbnail_embedding_and_ocr_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            backend = self._run_all_jobs_with_stubs(library_root)
            assets = list_assets(library_root)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                embedding_count = conn.execute("SELECT COUNT(*) FROM embedding_item").fetchone()[0]
                ocr_count = conn.execute("SELECT COUNT(*) FROM ocr_result").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual("indexed", assets.assets[0]["status"])
        self.assertIsNotNone(assets.assets[0]["thumbnail_url"])
        self.assertEqual(1, embedding_count)
        self.assertEqual(1, ocr_count)
        self.assertEqual(2048, backend.vector.shape[0])

    def test_text_search_uses_active_manifest_embedding_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            backend = self._run_all_jobs_with_stubs(library_root)
            with patch(
                "memesort_worker.library.get_embedding_backend", return_value=backend
            ):
                result = search_text(library_root, query="reaction", top_k=5)

        self.assertEqual(1, len(result.results))
        self.assertIn(" / vulkan", result.active_recipe_label)
        self.assertIn("visual", result.results[0]["match_sources"])

    def test_search_without_embeddings_does_not_start_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                result = search_text(library_root, query="reaction", top_k=5)

        self.assertEqual([], result.results)
        backend_factory.assert_not_called()

    def test_remove_last_source_record_deletes_managed_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, source_root = self._import_one_image(Path(temp_dir))
            asset = list_assets(library_root).assets[0]
            managed_path = library_root / str(asset["library_path"])

            result = remove_source_record(
                library_root,
                asset_id=str(asset["asset_id"]),
                source_path=str(source_root / "reaction.png"),
            )

            self.assertTrue(result.asset_deleted)
            self.assertFalse(managed_path.exists())
            self.assertEqual([], list_assets(library_root).assets)

    def test_delete_asset_removes_all_queued_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            asset_id = str(list_assets(library_root).assets[0]["asset_id"])

            result = delete_asset(library_root, asset_id)
            status = get_library_status(library_root)

        self.assertTrue(result.asset_deleted)
        self.assertEqual(3, result.removed_jobs)
        self.assertEqual(0, status.total_assets)
        self.assertEqual(0, status.total_jobs)

    def test_pending_job_deletion_keeps_non_requested_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                thumbnail_job_id = conn.execute(
                    "SELECT id FROM job WHERE type = 'generate_thumbnail'"
                ).fetchone()[0]
            finally:
                conn.close()

            result = delete_pending_jobs(library_root, [thumbnail_job_id])
            status = get_library_status(library_root)

        self.assertEqual([thumbnail_job_id], result.deleted_job_ids)
        self.assertEqual(2, status.total_jobs)

    def test_retry_failed_jobs_requeues_only_failed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                job_id = conn.execute("SELECT id FROM job ORDER BY id LIMIT 1").fetchone()[0]
                with conn:
                    conn.execute("UPDATE job SET status = 'failed' WHERE id = ?", (job_id,))
            finally:
                conn.close()

            result = retry_failed_jobs(library_root)

        self.assertEqual(1, result.retried_jobs)
        self.assertEqual(0, result.failed_jobs_remaining)

    def test_cli_init_library_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, io.StringIO() as output:
            with redirect_stdout(output):
                exit_code = run(["init-library", "--root", str(Path(temp_dir) / "library")])
            payload = json.loads(output.getvalue())

        self.assertEqual(0, exit_code)
        self.assertIn("created_recipe_id", payload)

    def test_app_state_exposes_one_manifest_runtime_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_app_state(Path(temp_dir) / "library").to_dict()

        self.assertEqual(get_runtime_descriptor().to_dict(), payload["runtime"])
        self.assertFalse(
            {"runtime_profiles", "model_variants", "runtime_settings"} & set(payload)
        )

    def test_web_state_endpoint_exposes_the_same_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(str(Path(temp_dir) / "library"))
            try:
                status, payload = self._request(app, "GET", "/api/state")
            finally:
                app.shutdown()

        self.assertEqual("200 OK", status)
        self.assertEqual(get_runtime_descriptor().to_dict(), payload["runtime"])
        self.assertNotIn("runtime_profiles", payload)
        self.assertNotIn("model_variants", payload)
        self.assertNotIn("runtime_settings", payload)


if __name__ == "__main__":
    unittest.main()
