"""Indexing pipeline tests using the injected runtime seam.

These run without an HTTP server or llama process: the pipeline receives a
fake runtime adapter instead of constructing backends itself.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from memesort_worker.embedding_backend import EmbeddingBackendError
from memesort_worker.indexing_pipeline import run_pending_jobs
from memesort_worker.library import DATABASE_NAME, import_folder
from memesort_worker.pinned_runtime import PinnedRuntime
from runtime_fakes import FakeEmbeddingBackend, FakeIndexingRuntime


class IndexingPipelineRuntimeInjectionTests(unittest.TestCase):
    def _import_one_image(self, root: Path) -> Path:
        library_root = root / "library"
        source_root = root / "source"
        source_root.mkdir()
        Image.new("RGB", (40, 30), (255, 0, 0)).save(source_root / "reaction.png", format="PNG")
        import_folder(library_root, source_root)
        return library_root

    def _job_statuses(self, library_root: Path) -> dict[str, tuple[str, str | None]]:
        conn = sqlite3.connect(library_root / DATABASE_NAME)
        try:
            rows = conn.execute("SELECT type, status, error_detail FROM job").fetchall()
        finally:
            conn.close()
        return {str(row[0]): (str(row[1]), row[2]) for row in rows}

    def test_unready_fake_produces_the_same_error_as_production(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_one_image(Path(temp_dir))
            production_runtime = PinnedRuntime(library_root)
            ready, message = production_runtime.is_ready_for_indexing()
            self.assertFalse(ready)

            with self.assertRaises(RuntimeError) as production_error:
                run_pending_jobs(library_root, production_runtime)
            with self.assertRaises(RuntimeError) as fake_error:
                run_pending_jobs(
                    library_root,
                    FakeIndexingRuntime(ready=False, ready_message=message),
                )

        self.assertEqual(str(production_error.exception), str(fake_error.exception))

    def test_fake_runtime_indexes_without_llama_or_http(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_one_image(Path(temp_dir))
            runtime = FakeIndexingRuntime()

            result = run_pending_jobs(library_root, runtime)

        self.assertEqual(3, result.completed_jobs)
        self.assertEqual(0, result.failed_jobs)
        self.assertEqual(runtime.embedding_backend.backend_id, result.backend)
        self.assertTrue(runtime.ocr_backend.closed)

    def test_embedding_failure_marks_only_the_embed_job_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_one_image(Path(temp_dir))
            runtime = FakeIndexingRuntime(
                embedding_backend=FakeEmbeddingBackend(
                    fail_with=EmbeddingBackendError("fake embed failed"),
                ),
            )

            result = run_pending_jobs(library_root, runtime)
            statuses = self._job_statuses(library_root)

        self.assertEqual(1, result.failed_jobs)
        self.assertEqual(2, result.completed_jobs)
        self.assertEqual("failed", statuses["embed_asset"][0])
        self.assertIn("fake embed failed", str(statuses["embed_asset"][1]))
        self.assertEqual("completed", statuses["generate_thumbnail"][0])
        self.assertEqual("completed", statuses["ocr_asset"][0])

    def test_cancelled_inference_fails_the_job_without_a_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_one_image(Path(temp_dir))
            runtime = FakeIndexingRuntime(
                embedding_backend=FakeEmbeddingBackend(cancel_requests=True),
            )

            result = run_pending_jobs(library_root, runtime)
            statuses = self._job_statuses(library_root)

        self.assertEqual(1, result.failed_jobs)
        self.assertEqual("failed", statuses["embed_asset"][0])
        self.assertIn("cancelled", str(statuses["embed_asset"][1]))


if __name__ == "__main__":
    unittest.main()
