from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

import numpy as np
from PIL import Image

from memesort_worker.app_state import build_app_state
from memesort_worker.cli import run
from memesort_worker.indexing_pipeline import run_pending_jobs
from memesort_worker.library import (
    BatchAssetActionResult,
    DATABASE_NAME,
    delete_asset,
    delete_pending_jobs,
    get_library_status,
    import_folder,
    initialize_library,
    list_assets,
    remove_source_record,
    rebuild_active_indexes,
    retry_failed_jobs,
    search_text,
)
from runtime_fakes import FakeIndexingRuntime
from memesort_worker.runtime_descriptor import get_runtime_descriptor
from memesort_worker.pinned_runtime import PinnedRuntime
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.inference_service import InferenceScheduler
from memesort_worker.library_store import LibraryStore
from memesort_worker.webapp import (
    ThreadedWSGIServer,
    create_app,
)


class QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return


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


class BlockingSearchBackend(StubEmbeddingBackend):
    def __init__(self, scheduler: InferenceScheduler) -> None:
        super().__init__()
        self.scheduler = scheduler
        self.started = threading.Event()
        self.release = threading.Event()
        self.text_calls: list[str] = []

    def embed_text(
        self,
        text: str,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        def operation() -> np.ndarray:
            self.text_calls.append(text)
            if len(self.text_calls) == 1:
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise RuntimeError("test search was not released")
            return super(BlockingSearchBackend, self).embed_text(
                text, output_dimension, instruction
            )

        return self.scheduler.submit(operation)


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

    def _run_all_jobs_with_stubs(
        self,
        library_root: Path,
        *,
        expected_completed_jobs: int = 3,
    ) -> StubEmbeddingBackend:
        backend = StubEmbeddingBackend()
        runtime = FakeIndexingRuntime(
            embedding_backend=backend,
            ocr_backend=StubOcrBackend(),
        )
        result = run_pending_jobs(library_root, runtime)

        self.assertEqual(0, result.failed_jobs)
        self.assertEqual(expected_completed_jobs, result.completed_jobs)
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

    def _http_json(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def _wait_for_search_queue(self, scheduler: InferenceScheduler, size: int) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with scheduler._condition:
                if len(scheduler._search_queue) == size:
                    return
            time.sleep(0.01)
        self.fail(f"search queue did not reach {size}")

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

    def test_import_records_image_dimensions(self) -> None:
        """Regression: default import must record actual width/height, not NULL."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            # _write_image creates a 40x30 PNG
            self._write_image(source_root / "sized.png")
            import_folder(library_root, source_root)
            assets = list_assets(library_root)

        self.assertEqual(1, len(assets.assets))
        self.assertEqual(40, assets.assets[0]["width"])
        self.assertEqual(30, assets.assets[0]["height"])

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
            runtime = FakeIndexingRuntime(embedding_backend=backend)
            result = search_text(library_root, query="reaction", top_k=5, runtime=runtime)

        self.assertEqual(1, len(result.results))
        self.assertIn(" / vulkan", result.active_recipe_label)
        self.assertIn("visual", result.results[0]["match_sources"])

    def test_search_without_embeddings_does_not_start_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            runtime = FakeIndexingRuntime()
            with patch.object(runtime, "get_embedding_backend") as backend_factory:
                result = search_text(library_root, query="reaction", top_k=5, runtime=runtime)

        self.assertEqual([], result.results)
        backend_factory.assert_not_called()

    def test_pending_jobs_require_current_session_vulkan_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            runtime = FakeIndexingRuntime(
                ready=False,
                ready_message="Vulkan runtime health has not been checked in this app session.",
            )
            with self.assertRaisesRegex(RuntimeError, "not authorized"):
                run_pending_jobs(library_root, runtime)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                claimed_jobs = conn.execute(
                    "SELECT COUNT(*) FROM job WHERE status != 'pending'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(0, claimed_jobs)

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

    def test_rebuild_active_index_only_requeues_selected_asset_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_image(source_root / "first.png", (255, 0, 0))
            self._write_image(source_root / "second.png", (0, 255, 0))
            import_folder(library_root, source_root)
            self._run_all_jobs_with_stubs(library_root, expected_completed_jobs=6)
            before_by_id = {
                str(asset["asset_id"]): asset for asset in list_assets(library_root).assets
            }
            selected_id, retained_id = sorted(before_by_id)

            result = rebuild_active_indexes(library_root, [selected_id])
            after_by_id = {
                str(asset["asset_id"]): asset for asset in list_assets(library_root).assets
            }
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                selected_embeddings = conn.execute(
                    "SELECT COUNT(*) FROM embedding_item WHERE asset_id = ?",
                    (selected_id,),
                ).fetchone()[0]
                retained_embeddings = conn.execute(
                    "SELECT COUNT(*) FROM embedding_item WHERE asset_id = ?",
                    (retained_id,),
                ).fetchone()[0]
                selected_ocr = conn.execute(
                    "SELECT COUNT(*) FROM ocr_result WHERE asset_id = ?",
                    (selected_id,),
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual([selected_id], result.affected_asset_ids)
        self.assertEqual(1, result.removed_embeddings)
        self.assertEqual(1, result.reindex_jobs_created)
        self.assertEqual("pending_initial_index", after_by_id[selected_id]["status"])
        self.assertEqual("indexed", after_by_id[retained_id]["status"])
        self.assertEqual(
            before_by_id[selected_id]["thumbnail_url"],
            after_by_id[selected_id]["thumbnail_url"],
        )
        self.assertEqual(
            before_by_id[selected_id]["source_record_count"],
            after_by_id[selected_id]["source_record_count"],
        )
        self.assertEqual(0, selected_embeddings)
        self.assertEqual(1, retained_embeddings)
        self.assertEqual(1, selected_ocr)

    def test_cli_init_library_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, io.StringIO() as output:
            with redirect_stdout(output):
                exit_code = run(["init-library", "--root", str(Path(temp_dir) / "library")])
            payload = json.loads(output.getvalue())

        self.assertEqual(0, exit_code)
        self.assertIn("created_recipe_id", payload)

    def test_cli_run_jobs_performs_a_session_health_check_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            output = io.StringIO()
            try:
                with patch(
                    "memesort_worker.cli.PinnedRuntime"
                ) as runtime_factory, patch(
                    "memesort_worker.cli.run_jobs"
                ) as run_jobs, redirect_stdout(output):
                    run_jobs.return_value.to_dict.return_value = {"processed_jobs": 0}

                    exit_code = run(["run-jobs", "--library-root", str(library_root)])
            finally:
                output_value = output.getvalue()
                output.close()

        self.assertEqual(0, exit_code)
        runtime_factory.assert_called_once_with(str(library_root))
        runtime = runtime_factory.return_value
        runtime.authorize.assert_called_once_with()
        run_jobs.assert_called_once_with(str(library_root), runtime, max_jobs=None)
        runtime.close.assert_called_once_with()
        self.assertEqual({"processed_jobs": 0}, json.loads(output_value))

    def test_app_state_exposes_one_manifest_runtime_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "memesort_worker.library_store.LibraryStore.list_asset_summaries",
                autospec=True,
                side_effect=LibraryStore.list_asset_summaries,
            ) as list_asset_summaries:
                payload = build_app_state(
                    Path(temp_dir) / "library", FakeIndexingRuntime()
                ).to_dict()

        self.assertEqual(get_runtime_descriptor().to_dict(), payload["runtime"])
        self.assertEqual(1, list_asset_summaries.call_count)
        self.assertFalse(
            {"runtime_profiles", "model_variants", "runtime_settings"} & set(payload)
        )

    def test_web_startup_runs_current_session_vulkan_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            runtime = PinnedRuntime(library_root)
            try:
                with patch(
                    "memesort_worker.pinned_runtime.run_runtime_health_check"
                ) as health_check:
                    health_check.return_value.smoke_test_ok = True
                    runtime.authorize()
            finally:
                runtime.close()

        health_check.assert_called_once_with(
            library_root=runtime.library_root,
            embedding_backend_factory=runtime.get_embedding_backend,
        )

    def test_web_startup_refuses_to_serve_when_vulkan_authorization_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            runtime = PinnedRuntime(library_root)
            try:
                with patch(
                    "memesort_worker.pinned_runtime.run_runtime_health_check"
                ) as health_check:
                    health_check.return_value.smoke_test_ok = False
                    health_check.return_value.error = "Vulkan0 is unavailable."
                    with self.assertRaisesRegex(RuntimeError, "Vulkan0 is unavailable"):
                        runtime.authorize()
            finally:
                runtime.close()

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

    def test_web_batch_rebuild_routes_selected_assets_to_active_index_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            app = create_app(str(library_root))
            expected = BatchAssetActionResult(
                library_root=str(library_root.resolve()),
                action="rebuild-active-index",
                requested_asset_ids=["asset-a"],
                affected_asset_ids=["asset-a"],
                skipped_running_asset_ids=[],
                removed_source_records=0,
                removed_jobs=0,
                removed_renditions=0,
                removed_embeddings=1,
                reindex_jobs_created=0,
            )
            try:
                with patch(
                    "memesort_worker.app_commands.rebuild_active_indexes",
                    return_value=expected,
                ) as rebuild:
                    status, payload = self._request(
                        app,
                        "POST",
                        "/api/assets/batch-action",
                        {"action": "rebuild-active-index", "asset_ids": ["asset-a"]},
                    )
            finally:
                app.shutdown()

        self.assertEqual("200 OK", status)
        self.assertEqual(expected.to_dict(), payload)
        rebuild.assert_called_once_with(library_root.resolve(), ["asset-a"])

    def test_web_resolve_asset_reveal_target_validates_the_managed_library_copy_without_opening_explorer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            asset_id = str(list_assets(library_root).assets[0]["asset_id"])
            app = create_app(str(library_root))
            try:
                with patch("memesort_worker.webapp.reveal_path_in_file_explorer") as reveal:
                    status, payload = self._request(
                        app,
                        "POST",
                        "/api/resolve-asset-reveal-target",
                        {"asset_id": asset_id, "target": "managed"},
                    )
            finally:
                app.shutdown()

            self.assertEqual("200 OK", status)
            self.assertEqual("managed", payload["target"])
            self.assertEqual(
                str((library_root / list_assets(library_root).assets[0]["library_path"]).resolve()),
                payload["resolved_path"],
            )
            reveal.assert_not_called()

    def test_http_search_cancellation_only_cancels_its_own_queued_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root, _ = self._import_one_image(Path(temp_dir))
            self._run_all_jobs_with_stubs(library_root)
            app = create_app(str(library_root))
            backend = BlockingSearchBackend(app.runtime.scheduler)
            server = make_server(
                "127.0.0.1",
                0,
                app,
                server_class=ThreadedWSGIServer,
                handler_class=QuietWSGIRequestHandler,
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            first_id = str(uuid.uuid4())
            cancelled_id = str(uuid.uuid4())
            responses: dict[str, tuple[int, dict[str, object]]] = {}
            first: threading.Thread | None = None
            cancelled: threading.Thread | None = None

            def search(request_id: str, query: str) -> None:
                responses[request_id] = self._http_json(
                    f"{base_url}/api/search?{urlencode({'query': query, 'top_k': 1, 'request_id': request_id})}"
                )

            try:
                with patch.object(
                    app.runtime, "get_embedding_backend", return_value=backend
                ):
                    first = threading.Thread(target=search, args=(first_id, "first"))
                    cancelled = threading.Thread(
                        target=search, args=(cancelled_id, "cancel me")
                    )
                    first.start()
                    self.assertTrue(backend.started.wait(timeout=2))
                    cancelled.start()
                    self._wait_for_search_queue(app.runtime.scheduler, 1)

                    cancel_status, cancel_payload = self._http_json(
                        f"{base_url}/api/search/cancel",
                        method="POST",
                        payload={"request_id": cancelled_id},
                    )
                    self.assertEqual(200, cancel_status)
                    self.assertEqual(cancelled_id, cancel_payload["request_id"])
                    self.assertTrue(cancel_payload["was_active"])

                    backend.release.set()
                    first.join(timeout=3)
                    cancelled.join(timeout=3)

                self.assertFalse(first.is_alive())
                self.assertFalse(cancelled.is_alive())
                self.assertEqual(200, responses[first_id][0])
                self.assertEqual(409, responses[cancelled_id][0])
                self.assertEqual("InferenceCancelledError", responses[cancelled_id][1]["error"])
                self.assertEqual(["first"], backend.text_calls)
            finally:
                backend.release.set()
                if first is not None:
                    first.join(timeout=3)
                if cancelled is not None:
                    cancelled.join(timeout=3)
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
