from __future__ import annotations

import io
import json
import os
import socket
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np

import memesort_worker.library as library_module
from scripts.evaluate_still_image_search import (
    _recall_at_k,
    build_query_text,
    prepare_recipe_for_eval,
)
from memesort_worker.cli import run
from memesort_worker.embedding_backend import (
    EmbeddingRuntimeConfig,
    EmbeddingBackendError,
    Qwen3VLEmbeddingBackend,
    _is_local_model_path,
    get_embedding_backend,
)
from memesort_worker.launcher import (
    default_library_root,
    launch_local_mvp_app,
    resolve_preferred_port,
)
from memesort_worker.desktop_entry import main as desktop_entry_main
from memesort_worker.library import (
    DATABASE_NAME,
    DEFAULT_RECIPE,
    apply_runtime_selection,
    delete_asset,
    delete_assets,
    discover_local_model_path,
    ensure_project_local_model_snapshot,
    find_similar_assets,
    get_asset_detail,
    get_last_health_check,
    get_library_status,
    project_model_store_root,
    is_runtime_ready_for_indexing,
    list_model_variants,
    get_runtime_config_for_profile,
    get_runtime_settings,
    get_setup_state,
    import_folder,
    initialize_library,
    list_assets,
    list_runtime_profiles,
    remove_source_record,
    resolve_effective_model_source,
    retry_failed_jobs,
    rebuild_active_indexes,
    run_pending_jobs,
    run_first_run_flow,
    run_runtime_health_check,
    scan_duplicate_assets,
    save_runtime_settings,
    search_text,
    search_image_path,
    switch_active_recipe,
)
from memesort_worker.semantic_retrieval import rank_asset_vector_rows
from memesort_worker.retrieval_composition import compose_text_search_results
from memesort_worker.webapp import create_app
from memesort_worker.app_runtime import WorkerLoopController
from memesort_worker.asset_browse import list_asset_summaries
from memesort_worker.app_state import build_app_state


class LibraryTests(unittest.TestCase):
    def test_prepare_recipe_for_eval_defaults_to_current_baseline(self) -> None:
        selection = prepare_recipe_for_eval(recipe_preset=None)

        self.assertIsNone(selection.preset_key)
        self.assertEqual("still-480-longest-side-v1", selection.preprocess_version)
        self.assertEqual(480, selection.still_max_side)
        self.assertEqual(480, selection.gif_max_side)

    def test_prepare_recipe_for_eval_supports_eval_only_preprocess_override(self) -> None:
        selection = prepare_recipe_for_eval(
            recipe_preset="qwen3-2b-cuda-balanced",
            recipe_runtime_profile="cuda-balanced-eval-1080",
            preprocess_version="still-1080-longest-side-v1",
            still_max_side=1080,
            gif_max_side=480,
        )

        self.assertIsNotNone(selection.preset_key)
        self.assertIn("qwen3-2b-cuda-balanced", str(selection.preset_key))
        self.assertEqual("still-1080-longest-side-v1", selection.preprocess_version)
        self.assertEqual(1080, selection.still_max_side)
        self.assertEqual(480, selection.gif_max_side)

    def test_initialize_library_creates_layout_and_default_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            result = initialize_library(library_root)

            self.assertTrue((library_root / DATABASE_NAME).exists())
            self.assertEqual(str(library_root.resolve()), result.library_root)
            self.assertTrue(result.created_database)

            for relative_dir in (
                "originals",
                "thumbnails",
                "frames",
                "contact_sheets",
                "models",
                "runtime",
                "logs",
            ):
                self.assertTrue((library_root / relative_dir).is_dir(), relative_dir)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                recipe_count = conn.execute("SELECT COUNT(*) FROM embedding_recipe").fetchone()[0]
                worker_state = conn.execute(
                    "SELECT value_json FROM worker_state WHERE key = 'active_recipe_id'"
                ).fetchone()[0]
                active_recipe_id = json.loads(worker_state)["recipe_id"]
                active_recipe = conn.execute(
                    "SELECT model_id, output_dimension FROM embedding_recipe WHERE id = ?",
                    (active_recipe_id,),
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(1, recipe_count)
            self.assertEqual("Qwen/Qwen3-VL-Embedding-2B", active_recipe[0])
            self.assertEqual(2048, active_recipe[1])

    def test_get_embedding_backend_requires_model_name_for_qwen(self) -> None:
        with self.assertRaisesRegex(EmbeddingBackendError, "model_name_or_path"):
            get_embedding_backend("qwen3-vl")

    def test_is_local_model_path_distinguishes_local_paths_from_hf_ids(self) -> None:
        self.assertFalse(_is_local_model_path("Qwen/Qwen3-VL-Embedding-2B"))
        self.assertTrue(_is_local_model_path(r"C:\models\Qwen3-VL-Embedding-2B"))

    def test_build_query_text_joins_selected_fields(self) -> None:
        query = build_query_text(
            {
                "ocr_translation": "Did you ask?",
                "people_appearance": ["anime male", "dark hair"],
                "themes": ["sarcastic", "dismissive"],
                "objects": [],
            },
            ["ocr_translation", "people_appearance", "themes"],
        )
        self.assertEqual(
            "Did you ask?\nanime male, dark hair\nsarcastic, dismissive",
            query,
        )

    def test_recall_at_k_counts_hits_within_cutoff(self) -> None:
        self.assertEqual(0.5, _recall_at_k([1, 6, None, 3], 3))

    def test_qwen_pool_last_token_uses_last_unmasked_position(self) -> None:
        backend = object.__new__(Qwen3VLEmbeddingBackend)
        backend._torch = __import__("torch")
        hidden = backend._torch.tensor(
            [
                [
                    [1.0, 0.0],
                    [2.0, 0.0],
                    [3.0, 0.0],
                    [4.0, 0.0],
                ]
            ]
        )
        mask = backend._torch.tensor([[1, 1, 1, 0]])
        pooled = backend._pool_last_token(hidden, mask)
        self.assertEqual((1, 2), tuple(pooled.shape))
        self.assertTrue(backend._torch.equal(pooled, backend._torch.tensor([[3.0, 0.0]])))

    def test_get_embedding_backend_reuses_cached_qwen_instance(self) -> None:
        runtime = EmbeddingRuntimeConfig(
            model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
            torch_dtype="float16",
            device="cpu",
        )
        with patch("memesort_worker.embedding_backend.Qwen3VLEmbeddingBackend") as backend_cls:
            backend_cls.return_value = object()
            first = get_embedding_backend("qwen3-vl", runtime)
            second = get_embedding_backend("qwen3-vl", runtime)

        self.assertIs(first, second)
        backend_cls.assert_called_once_with(runtime)

    def test_qwen_backend_requires_tokenizer_runtime_dependency(self) -> None:
        runtime = EmbeddingRuntimeConfig(
            model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
            torch_dtype="float16",
            device="cpu",
        )

        real_import_module = __import__("importlib").import_module

        def fake_import_module(name: str, package=None):
            if name in {"sentencepiece", "tiktoken"}:
                raise ImportError(name)
            return real_import_module(name, package)

        with patch("importlib.import_module", side_effect=fake_import_module):
            with self.assertRaisesRegex(
                EmbeddingBackendError,
                "tokenizer support requires `sentencepiece` or `tiktoken`",
            ):
                Qwen3VLEmbeddingBackend(runtime)

    def test_get_embedding_backend_distinguishes_thread_configurations(self) -> None:
        runtime_a = EmbeddingRuntimeConfig(
            model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
            torch_dtype="float16",
            device="cpu",
            num_threads=8,
        )
        runtime_b = EmbeddingRuntimeConfig(
            model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
            torch_dtype="float16",
            device="cpu",
            num_threads=10,
        )
        with patch("memesort_worker.embedding_backend.Qwen3VLEmbeddingBackend") as backend_cls:
            backend_cls.side_effect = [object(), object()]
            first = get_embedding_backend("qwen3-vl", runtime_a)
            second = get_embedding_backend("qwen3-vl", runtime_b)

        self.assertIsNot(first, second)
        self.assertEqual(2, backend_cls.call_count)

    def test_qwen_backend_does_not_reset_same_interop_threads(self) -> None:
        backend = object.__new__(Qwen3VLEmbeddingBackend)

        class FakeTorch:
            def __init__(self) -> None:
                self.num_threads = 8
                self.interop_threads = 2
                self.set_num_threads_calls = 0
                self.set_num_interop_threads_calls = 0

            def get_num_threads(self) -> int:
                return self.num_threads

            def set_num_threads(self, value: int) -> None:
                self.num_threads = value
                self.set_num_threads_calls += 1

            def get_num_interop_threads(self) -> int:
                return self.interop_threads

            def set_num_interop_threads(self, value: int) -> None:
                self.interop_threads = value
                self.set_num_interop_threads_calls += 1

        fake_torch = FakeTorch()
        backend._torch = fake_torch

        import memesort_worker.embedding_backend as embedding_backend_module

        previous = embedding_backend_module._CONFIGURED_TORCH_INTEROP_THREADS
        embedding_backend_module._CONFIGURED_TORCH_INTEROP_THREADS = 2
        try:
            backend._configure_torch_threads(
                EmbeddingRuntimeConfig(
                    model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                    num_threads=8,
                    num_interop_threads=2,
                )
            )
        finally:
            embedding_backend_module._CONFIGURED_TORCH_INTEROP_THREADS = previous

        self.assertEqual(0, fake_torch.set_num_threads_calls)
        self.assertEqual(0, fake_torch.set_num_interop_threads_calls)

    def test_import_folder_coalesces_duplicate_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            nested_root = source_root / "nested"
            source_root.mkdir()
            nested_root.mkdir()

            (source_root / "same-a.jpg").write_bytes(b"same-content")
            (nested_root / "same-b.jpg").write_bytes(b"same-content")
            (source_root / "different.png").write_bytes(b"different-content")
            (source_root / "notes.txt").write_text("ignore me", encoding="utf-8")

            first_result = import_folder(library_root, source_root)
            self.assertEqual(4, first_result.discovered_files)
            self.assertEqual(3, first_result.supported_files)
            self.assertEqual(1, first_result.unsupported_files)
            self.assertEqual(2, first_result.new_assets)
            self.assertEqual(1, first_result.duplicate_assets)
            self.assertEqual(3, first_result.source_records_added)
            self.assertEqual(0, first_result.source_records_refreshed)
            self.assertEqual(6, first_result.jobs_created)

            second_result = import_folder(library_root, source_root)
            self.assertEqual(0, second_result.new_assets)
            self.assertEqual(3, second_result.duplicate_assets)
            self.assertEqual(0, second_result.source_records_added)
            self.assertEqual(3, second_result.source_records_refreshed)
            self.assertEqual(0, second_result.jobs_created)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                asset_count = conn.execute("SELECT COUNT(*) FROM asset").fetchone()[0]
                source_count = conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0]
                job_count = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(2, asset_count)
            self.assertEqual(3, source_count)
            self.assertEqual(6, job_count)

    def test_asset_summary_keeps_list_fields_without_detail_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "summary.png", (255, 0, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            asset = list_asset_summaries(library_root).assets[0]

        self.assertEqual("indexed", asset["status"])
        self.assertEqual(1, asset["source_record_count"])
        self.assertEqual("summary.png", Path(asset["source_records"][0]["source_path"]).name)
        self.assertNotIn("jobs", asset)
        self.assertNotIn("ocr_results", asset)
        self.assertNotIn("renditions", asset)

    def test_cli_init_library_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = run(["init-library", "--root", temp_dir])

            self.assertEqual(0, exit_code)
            payload = json.loads(output.getvalue())
            self.assertEqual(str(Path(temp_dir).resolve()), payload["library_root"])
            self.assertTrue((Path(temp_dir) / DATABASE_NAME).exists())

    def test_cli_run_jobs_passes_runtime_backend_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("memesort_worker.cli.run_pending_jobs") as run_jobs_mock:
                run_jobs_mock.return_value.to_dict.return_value = {"ok": True}
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = run(
                        [
                            "run-jobs",
                            "--library-root",
                            temp_dir,
                            "--backend",
                            "qwen3-vl",
                            "--model-name-or-path",
                            "Qwen/Qwen3-VL-Embedding-2B",
                            "--torch-dtype",
                            "float32",
                            "--device",
                            "cpu",
                            "--num-threads",
                            "8",
                            "--num-interop-threads",
                            "2",
                            "--max-jobs",
                            "3",
                        ]
                    )

            self.assertEqual(0, exit_code)
            run_jobs_mock.assert_called_once_with(
                temp_dir,
                backend_name="qwen3-vl",
                model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                torch_dtype="float32",
                device="cpu",
                num_threads=8,
                num_interop_threads=2,
                max_jobs=3,
            )

    def test_cli_search_passes_runtime_backend_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("memesort_worker.cli.search_text") as search_mock:
                search_mock.return_value.to_dict.return_value = {"ok": True}
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = run(
                        [
                            "search",
                            "--library-root",
                            temp_dir,
                            "--query",
                            "red meme",
                            "--backend",
                            "qwen3-vl",
                            "--model-name-or-path",
                            "F:\\models\\Qwen3-VL-Embedding-2B",
                            "--torch-dtype",
                            "bfloat16",
                            "--device",
                            "cuda:0",
                            "--num-threads",
                            "10",
                            "--num-interop-threads",
                            "3",
                            "--top-k",
                            "7",
                        ]
                    )

            self.assertEqual(0, exit_code)
            search_mock.assert_called_once_with(
                temp_dir,
                query="red meme",
                top_k=7,
                backend_name="qwen3-vl",
                model_name_or_path="F:\\models\\Qwen3-VL-Embedding-2B",
                torch_dtype="bfloat16",
                device="cuda:0",
                num_threads=10,
                num_interop_threads=3,
            )

    def test_cli_search_image_passes_runtime_backend_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("memesort_worker.cli.search_image_path") as search_mock:
                search_mock.return_value.to_dict.return_value = {"ok": True}
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = run(
                        [
                            "search-image",
                            "--library-root",
                            temp_dir,
                            "--path",
                            "F:\\images\\reaction.gif",
                            "--backend",
                            "qwen3-vl",
                            "--model-name-or-path",
                            "F:\\models\\Qwen3-VL-Embedding-2B",
                            "--torch-dtype",
                            "float16",
                            "--device",
                            "cuda:0",
                            "--num-threads",
                            "6",
                            "--num-interop-threads",
                            "2",
                            "--top-k",
                            "9",
                        ]
                    )

            self.assertEqual(0, exit_code)
            search_mock.assert_called_once_with(
                temp_dir,
                image_path="F:\\images\\reaction.gif",
                top_k=9,
                backend_name="qwen3-vl",
                model_name_or_path="F:\\models\\Qwen3-VL-Embedding-2B",
                torch_dtype="float16",
                device="cuda:0",
                num_threads=6,
                num_interop_threads=2,
            )

    def test_cli_switch_recipe_passes_gif_frame_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("memesort_worker.cli.switch_active_recipe") as switch_mock:
                switch_mock.return_value.to_dict.return_value = {"ok": True}
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = run(
                        [
                            "switch-recipe",
                            "--library-root",
                            temp_dir,
                            "--preset",
                            "qwen3-2b-cpu",
                            "--gif-frame-count",
                            "6",
                        ]
                    )

        self.assertEqual(0, exit_code)
        switch_mock.assert_called_once_with(
            temp_dir,
            "qwen3-2b-cpu",
            gif_frame_count=6,
        )

    def test_cli_launch_app_passes_expected_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("memesort_worker.cli.launch_local_mvp_app") as launch_mock:
                exit_code = run(
                    [
                        "launch-app",
                        "--library-root",
                        temp_dir,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "9000",
                        "--no-browser",
                    ]
                )

        self.assertEqual(0, exit_code)
        launch_mock.assert_called_once_with(
            library_root=temp_dir,
            host="127.0.0.1",
            preferred_port=9000,
            open_browser=False,
        )

    def test_cli_desktop_shell_calls_launcher(self) -> None:
        with patch("memesort_worker.cli.launch_desktop_shell") as desktop_mock:
            exit_code = run(["desktop-shell"])

        self.assertEqual(0, exit_code)
        desktop_mock.assert_called_once_with()

    def test_desktop_entry_uses_environment_for_autostart(self) -> None:
        env = {
            "MEMESORT_LIBRARY_ROOT": r"F:\meme-lib",
            "MEMESORT_IMPORT_SOURCE": r"F:\source-memes",
            "MEMESORT_AUTOSTART_UI": "1",
            "MEMESORT_OPEN_BROWSER": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("memesort_worker.desktop_entry.launch_desktop_shell") as launch_mock:
                desktop_entry_main()

        launch_mock.assert_called_once_with(
            library_root=r"F:\meme-lib",
            autostart_ui=True,
            import_source=r"F:\source-memes",
            open_ui_on_ready=False,
        )

    def test_list_assets_projects_pending_initial_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            (source_root / "first.jpg").write_bytes(b"image-one")

            import_folder(library_root, source_root)
            result = list_assets(library_root)

            self.assertEqual(1, len(result.assets))
            self.assertEqual("pending_initial_index", result.assets[0]["status"])
            self.assertEqual(1, result.assets[0]["source_record_count"])

    def test_switch_recipe_schedules_reindex_and_status_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            (source_root / "first.jpg").write_bytes(b"image-one")

            import_folder(library_root, source_root)
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                active_recipe_id = conn.execute(
                    "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state WHERE key = 'active_recipe_id'"
                ).fetchone()[0]
                asset_id = conn.execute("SELECT id FROM asset").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO embedding_item (
                        id, asset_id, recipe_id, kind, source_ref, vector_dim, vector_blob, created_at
                    ) VALUES (?, ?, ?, 'image', ?, ?, ?, ?)
                    """,
                    (
                        "embedding-1",
                        asset_id,
                        active_recipe_id,
                        "original",
                        2048,
                        sqlite3.Binary(b"0" * 16),
                        "2026-05-22T00:00:00+00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            switch_result = switch_active_recipe(library_root, "qwen3-8b-cpu")
            self.assertEqual(1, switch_result.reindex_jobs_created)

            asset_result = list_assets(library_root)
            self.assertEqual(
                "Qwen3-VL-Embedding-8B / 4096d / cpu-low-memory",
                asset_result.active_recipe_label,
            )
            self.assertEqual("reindex_pending", asset_result.assets[0]["status"])
            self.assertEqual(
                ["Qwen3-VL-Embedding-2B / 2048d / cpu-low-memory"],
                asset_result.assets[0]["stale_recipe_labels"],
            )

    def test_switch_recipe_with_new_gif_frame_count_creates_distinct_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_gif(source_root / "animated.gif")

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            switch_result = switch_active_recipe(
                library_root,
                "qwen3-2b-cpu",
                gif_frame_count=6,
            )
            self.assertEqual(1, switch_result.reindex_jobs_created)

            asset_result = list_assets(library_root)
            self.assertEqual(
                "Qwen3-VL-Embedding-2B / 2048d / cpu-low-memory / gif-f6",
                asset_result.active_recipe_label,
            )
            self.assertEqual(
                ["Qwen3-VL-Embedding-2B / 2048d / cpu-low-memory"],
                asset_result.assets[0]["stale_recipe_labels"],
            )

    def test_run_jobs_generates_thumbnail_and_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0), size=(24, 40))

            import_folder(library_root, source_root)
            result = run_pending_jobs(library_root, backend_name="debug")
            self.assertEqual(3, result.processed_jobs)
            self.assertEqual(3, result.completed_jobs)
            self.assertEqual(0, result.failed_jobs)
            self.assertEqual(0, result.requeued_running_jobs)
            self.assertEqual(0, result.retried_failed_jobs)

            asset_result = list_assets(library_root)
            asset = asset_result.assets[0]
            self.assertEqual("indexed", asset["status"])
            self.assertEqual(
                ["Qwen3-VL-Embedding-2B / 2048d / cpu-low-memory"],
                asset["indexed_recipe_labels"],
            )
            self.assertTrue(any((library_root / "thumbnails").iterdir()))

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                dimensions = conn.execute(
                    "SELECT width, height FROM asset"
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual((24, 40), dimensions)

    def test_run_jobs_stores_ocr_result_for_still_images_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "hello_text.png", (255, 0, 0), size=(24, 40))
            self._write_demo_gif(source_root / "animated.gif")

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                ocr_count = conn.execute("SELECT COUNT(*) FROM ocr_result").fetchone()[0]
                ocr_text = conn.execute("SELECT searchable_text FROM ocr_result").fetchone()[0]
                ocr_job_count = conn.execute("SELECT COUNT(*) FROM job WHERE type = 'ocr_asset'").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(1, ocr_count)
            self.assertEqual(1, ocr_job_count)
            self.assertTrue(str(ocr_text).strip())

    def test_initialize_library_backfills_missing_ocr_jobs_for_existing_stills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "legacy.png", (255, 0, 0))
            self._write_demo_gif(source_root / "legacy.gif")

            import_folder(library_root, source_root)
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                conn.execute("DELETE FROM job WHERE type = 'ocr_asset'")
                conn.commit()
            finally:
                conn.close()

            initialize_library(library_root)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                ocr_jobs = conn.execute(
                    "SELECT COUNT(*) FROM job WHERE type = 'ocr_asset'"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(1, ocr_jobs)

    def test_run_jobs_respects_recipe_preprocess_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "large.png", (255, 0, 0), size=(2400, 1800))

            import_folder(library_root, source_root)

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.backend_id = "fake-backend"
                backend.embed_image_bytes.return_value = self._unit_vector(2048)
                backend.embed_text.return_value = self._unit_vector(2048)

                result = run_pending_jobs(library_root, backend_name="debug")

            self.assertEqual(3, result.completed_jobs)
            backend.embed_image_bytes.assert_called_once()
            image_bytes = backend.embed_image_bytes.call_args.args[0]

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as processed:
                self.assertLessEqual(max(processed.size), 480)
                self.assertEqual((480, 360), processed.size)

    def test_switch_recipe_to_cuda_quality_uses_distinct_runtime_profile_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0), size=(320, 240))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            switch_result = switch_active_recipe(library_root, "qwen3-2b-cuda-quality")

            self.assertEqual(
                "Qwen3-VL-Embedding-2B / 2048d / cuda-quality",
                switch_result.active_recipe_label,
            )
            self.assertEqual(1, switch_result.reindex_jobs_created)

            asset_result = list_assets(library_root)
            self.assertEqual("reindex_pending", asset_result.assets[0]["status"])
            self.assertEqual(
                ["Qwen3-VL-Embedding-2B / 2048d / cpu-low-memory"],
                asset_result.assets[0]["stale_recipe_labels"],
            )

    def test_cuda_quality_still_preprocess_keeps_native_size_under_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "still.png", (255, 0, 0), size=(1200, 900))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            switch_active_recipe(library_root, "qwen3-2b-cuda-quality")

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.backend_id = "fake-backend"
                backend.embed_image_bytes.return_value = self._unit_vector(2048)

                run_pending_jobs(library_root, backend_name="debug", max_jobs=1)

            image_bytes = backend.embed_image_bytes.call_args.args[0]

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as processed:
                self.assertEqual((1200, 900), processed.size)

    def test_cuda_quality_still_preprocess_downscales_over_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "still.png", (255, 0, 0), size=(3000, 1800))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            switch_active_recipe(library_root, "qwen3-2b-cuda-quality")

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.backend_id = "fake-backend"
                backend.embed_image_bytes.return_value = self._unit_vector(2048)

                run_pending_jobs(library_root, backend_name="debug", max_jobs=1)

            image_bytes = backend.embed_image_bytes.call_args.args[0]

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as processed:
                self.assertEqual((1536, 922), processed.size)

    def test_cuda_quality_gif_preprocess_uses_gif_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            gif_path = source_root / "animated.gif"
            self._write_demo_gif(gif_path, size=(1400, 700))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            switch_active_recipe(library_root, "qwen3-2b-cuda-quality")

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.backend_id = "fake-backend"
                backend.embed_image_bytes.return_value = self._unit_vector(2048)

                run_pending_jobs(library_root, backend_name="debug", max_jobs=1)

            frame_bytes = backend.embed_image_bytes.call_args_list[0].args[0]

            from PIL import Image

            with Image.open(io.BytesIO(frame_bytes)) as processed:
                self.assertEqual((960, 480), processed.size)

    def test_run_jobs_embeds_multiple_gif_frames_for_one_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            gif_path = source_root / "animated.gif"
            self._write_demo_gif(gif_path)

            import_folder(library_root, source_root)

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.backend_id = "fake-backend"
                backend.embed_image_bytes.return_value = self._unit_vector(2048)

                result = run_pending_jobs(library_root, backend_name="debug")

            self.assertEqual(2, result.completed_jobs)
            self.assertEqual(4, backend.embed_image_bytes.call_count)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                rows = conn.execute(
                    """
                    SELECT source_ref
                    FROM embedding_item
                    ORDER BY source_ref ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(
                ["frame:0", "frame:1", "frame:2", "frame:3"],
                [row[0] for row in rows],
            )

    def test_run_jobs_requeues_running_and_failed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                conn.execute("UPDATE job SET status = 'running' WHERE type = 'generate_thumbnail'")
                conn.execute("UPDATE job SET status = 'failed' WHERE type = 'embed_asset'")
                conn.commit()
            finally:
                conn.close()

            result = run_pending_jobs(library_root, backend_name="debug")

            self.assertEqual(1, result.requeued_running_jobs)
            self.assertEqual(1, result.retried_failed_jobs)
            self.assertEqual(3, result.completed_jobs)

    def test_rank_vector_rows_aggregates_multiple_vectors_per_asset_by_max(self) -> None:
        query = np.array([1.0, 0.0], dtype=np.float32)
        vector_rows = [
            self._fake_vector_row("asset-a", np.array([0.2, 0.0], dtype=np.float32), "a.png"),
            self._fake_vector_row("asset-a", np.array([0.9, 0.0], dtype=np.float32), "a.png"),
            self._fake_vector_row("asset-b", np.array([0.7, 0.0], dtype=np.float32), "b.png"),
        ]

        results = rank_asset_vector_rows(query, vector_rows, top_k=5)

        self.assertEqual(2, len(results))
        self.assertEqual("asset-a", results[0]["asset_id"])
        self.assertAlmostEqual(0.9, results[0]["score"])
        self.assertEqual("asset-b", results[1]["asset_id"])

    def test_rank_vector_rows_aggregates_multiple_query_vectors_by_max(self) -> None:
        query_vectors = [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]
        vector_rows = [
            self._fake_vector_row("asset-a", np.array([0.8, 0.0], dtype=np.float32), "a.png"),
            self._fake_vector_row("asset-b", np.array([0.0, 0.95], dtype=np.float32), "b.png"),
        ]

        results = rank_asset_vector_rows(query_vectors, vector_rows, top_k=5)

        self.assertEqual(2, len(results))
        self.assertEqual("asset-b", results[0]["asset_id"])
        self.assertAlmostEqual(0.95, results[0]["score"])
        self.assertEqual("asset-a", results[1]["asset_id"])

    def test_compose_text_search_results_merges_visual_and_ocr_matches(self) -> None:
        results = compose_text_search_results(
            visual_results=[
                {
                    "asset_id": "same",
                    "score": 0.82,
                    "library_path": "originals/same.png",
                    "library_url": "/media/originals/same.png",
                    "thumbnail_url": "/media/thumbnails/same.jpg",
                    "media_type": "image/png",
                    "content_hash": "same-hash",
                }
            ],
            ocr_results=[
                {
                    "asset_id": "same",
                    "score": 0.0,
                    "library_path": "originals/same.png",
                    "library_url": "/media/originals/same.png",
                    "thumbnail_url": "/media/thumbnails/same.jpg",
                    "media_type": "image/png",
                    "content_hash": "same-hash",
                    "ocr_score": -1.2,
                    "ocr_confidence": 0.91,
                    "ocr_snippet": "hidden text",
                    "ocr_text": "hidden text",
                }
            ],
            top_k=5,
        )

        self.assertEqual(1, len(results))
        self.assertEqual(["visual", "ocr"], results[0]["match_sources"])
        self.assertEqual(0.82, results[0]["visual_score"])
        self.assertEqual("hidden text", results[0]["ocr_snippet"])
        self.assertGreater(float(results[0]["score"]), 0.0)

    def test_compose_text_search_results_preserves_ocr_only_match(self) -> None:
        results = compose_text_search_results(
            visual_results=[],
            ocr_results=[
                {
                    "asset_id": "ocr-only",
                    "score": 0.0,
                    "library_path": "originals/ocr.png",
                    "library_url": "/media/originals/ocr.png",
                    "thumbnail_url": "/media/thumbnails/ocr-only.jpg",
                    "media_type": "image/png",
                    "content_hash": "ocr-hash",
                    "ocr_score": -0.5,
                    "ocr_confidence": 0.88,
                    "ocr_snippet": "needle",
                    "ocr_text": "needle",
                }
            ],
            top_k=5,
        )

        self.assertEqual(1, len(results))
        self.assertEqual(["ocr"], results[0]["match_sources"])
        self.assertEqual("needle", results[0]["ocr_snippet"])

    def test_compose_text_search_results_applies_top_k_after_fusion(self) -> None:
        results = compose_text_search_results(
            visual_results=[
                {"asset_id": "visual-a", "score": 0.9},
                {"asset_id": "visual-b", "score": 0.8},
            ],
            ocr_results=[
                {"asset_id": "ocr-a", "score": 0.0, "ocr_snippet": "a"},
                {"asset_id": "ocr-b", "score": 0.0, "ocr_snippet": "b"},
            ],
            top_k=2,
        )

        self.assertEqual(2, len(results))
        self.assertEqual("visual-a", results[0]["asset_id"])
        self.assertEqual("ocr-a", results[1]["asset_id"])

    def test_search_and_similarity_work_with_debug_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            self._write_demo_image(source_root / "second.png", (0, 255, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            asset_result = list_assets(library_root)
            first_asset_id = asset_result.assets[0]["asset_id"]

            search_result = search_text(
                library_root,
                query="red meme",
                top_k=2,
                backend_name="debug",
            )
            self.assertEqual(2, len(search_result.results))
            self.assertEqual("original", search_result.results[0]["matched_source_ref"])
            self.assertTrue(search_result.results[0]["library_url"].startswith("/media/originals/"))
            self.assertTrue(search_result.results[0]["thumbnail_url"].startswith("/media/thumbnails/"))

            similar_result = find_similar_assets(
                library_root,
                asset_id=first_asset_id,
                top_k=1,
            )
            self.assertEqual(1, len(similar_result.results))
            self.assertNotEqual(first_asset_id, similar_result.results[0]["asset_id"])
            self.assertTrue(similar_result.results[0]["library_url"].startswith("/media/originals/"))
            self.assertTrue(similar_result.results[0]["thumbnail_url"].startswith("/media/thumbnails/"))

    def test_search_image_path_works_with_debug_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            first_path = source_root / "first.png"
            second_path = source_root / "second.png"
            self._write_demo_image(first_path, (255, 0, 0))
            self._write_demo_image(second_path, (0, 255, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            target_asset = next(
                asset
                for asset in list_assets(library_root).assets
                if any(record["source_path"] == str(first_path) for record in asset["source_records"])
            )

            result = search_image_path(
                library_root,
                image_path=first_path,
                top_k=2,
                backend_name="debug",
            )

            self.assertEqual(str(first_path.resolve()), result.query_path)
            self.assertEqual("image/png", result.query_media_type)
            self.assertEqual(2, len(result.results))
            self.assertEqual(target_asset["asset_id"], result.results[0]["asset_id"])
            self.assertTrue(result.results[0]["thumbnail_url"].startswith("/media/thumbnails/"))

    def test_find_similar_assets_uses_all_query_vectors_for_gif_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            initialize_library(library_root)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                active_recipe_id = conn.execute(
                    "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state WHERE key = 'active_recipe_id'"
                ).fetchone()[0]
                now = "2026-05-22T00:00:00+00:00"
                asset_rows = [
                    ("gif-query", "originals/query.gif", "image/gif", "hash-query"),
                    ("candidate-a", "originals/a.gif", "image/gif", "hash-a"),
                    ("candidate-b", "originals/b.gif", "image/gif", "hash-b"),
                ]
                for asset_id, library_path, media_type, content_hash in asset_rows:
                    conn.execute(
                        """
                        INSERT INTO asset (
                            id, library_path, media_type, content_hash, byte_size, width, height,
                            imported_at, updated_at, deleted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (asset_id, library_path, media_type, content_hash, 1, 24, 24, now, now),
                    )

                embedding_rows = [
                    ("gif-query", "frame:0", np.array([1.0, 0.0], dtype=np.float32)),
                    ("gif-query", "frame:3", np.array([0.0, 1.0], dtype=np.float32)),
                    ("candidate-a", "frame:0", np.array([0.9, 0.0], dtype=np.float32)),
                    ("candidate-b", "frame:0", np.array([0.0, 0.95], dtype=np.float32)),
                ]
                for index, (asset_id, source_ref, vector) in enumerate(embedding_rows, start=1):
                    conn.execute(
                        """
                        INSERT INTO embedding_item (
                            id, asset_id, recipe_id, kind, source_ref, vector_dim, vector_blob, created_at
                        ) VALUES (?, ?, ?, 'image', ?, ?, ?, ?)
                        """,
                        (
                            f"embedding-{index}",
                            asset_id,
                            active_recipe_id,
                            source_ref,
                            int(vector.shape[0]),
                            sqlite3.Binary(vector.astype(np.float32).tobytes()),
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

            similar_result = find_similar_assets(
                library_root,
                asset_id="gif-query",
                top_k=2,
            )

            self.assertEqual(2, len(similar_result.results))
            self.assertEqual("candidate-b", similar_result.results[0]["asset_id"])
            self.assertAlmostEqual(0.95, similar_result.results[0]["score"])
            self.assertEqual("frame:0", similar_result.results[0]["matched_source_ref"])
            self.assertEqual("candidate-a", similar_result.results[1]["asset_id"])

    def test_runtime_profiles_include_cuda_quality(self) -> None:
        profiles = {profile.profile_id: profile for profile in list_runtime_profiles()}

        self.assertIn("cpu-low-memory", profiles)
        self.assertIn("cuda-balanced", profiles)
        self.assertIn("cuda-quality", profiles)
        self.assertEqual(1536, profiles["cuda-quality"].still_max_side)
        self.assertEqual(960, profiles["cuda-quality"].gif_max_side)

    def test_model_variants_include_2b_and_8b(self) -> None:
        variants = {variant.model_key: variant for variant in list_model_variants()}

        self.assertIn("qwen3-2b", variants)
        self.assertIn("qwen3-8b", variants)
        self.assertEqual(2048, variants["qwen3-2b"].output_dimension)
        self.assertEqual(4096, variants["qwen3-8b"].output_dimension)

    def test_discover_local_model_path_ignores_global_cache_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_home = Path(temp_dir)
            snapshot = (
                fake_home
                / ".cache"
                / "huggingface"
                / "hub"
                / "models--Qwen--Qwen3-VL-Embedding-2B"
                / "snapshots"
                / "abc123"
            )
            snapshot.mkdir(parents=True)
            refs_main = snapshot.parents[1] / "refs" / "main"
            refs_main.parent.mkdir(parents=True, exist_ok=True)
            refs_main.write_text("abc123", encoding="utf-8")

            with patch("memesort_worker.library.project_root", return_value=fake_home / "project"):
                with patch("memesort_worker.library.Path.home", return_value=fake_home):
                    result = discover_local_model_path("qwen3-2b")

        self.assertIsNone(result)

    def test_discover_local_model_path_prefers_project_local_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            snapshot = (
                project_root
                / ".models"
                / "models--Qwen--Qwen3-VL-Embedding-2B"
                / "snapshots"
                / "project123"
            )
            snapshot.mkdir(parents=True)
            refs_main = snapshot.parents[1] / "refs" / "main"
            refs_main.parent.mkdir(parents=True, exist_ok=True)
            refs_main.write_text("project123", encoding="utf-8")

            with patch("memesort_worker.library.project_root", return_value=project_root):
                result = discover_local_model_path("qwen3-2b")

        self.assertEqual(str(snapshot), result)

    def test_project_model_store_root_is_repo_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            with patch("memesort_worker.library.project_root", return_value=project_root):
                result = project_model_store_root()

        self.assertEqual(project_root / ".models", result)

    def test_ensure_project_local_model_snapshot_downloads_into_project_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            downloaded_snapshot = (
                project_root
                / ".models"
                / "models--Qwen--Qwen3-VL-Embedding-2B"
                / "snapshots"
                / "downloaded123"
            )
            downloaded_snapshot.mkdir(parents=True)
            refs_main = downloaded_snapshot.parents[1] / "refs" / "main"
            refs_main.parent.mkdir(parents=True, exist_ok=True)
            refs_main.write_text("downloaded123", encoding="utf-8")

            with patch("memesort_worker.library.project_root", return_value=project_root):
                with patch(
                    "memesort_worker.library._discover_snapshot_for_model_id",
                    side_effect=[None, None],
                ):
                    with patch("huggingface_hub.snapshot_download", return_value=str(downloaded_snapshot)) as download_mock:
                        result = ensure_project_local_model_snapshot("Qwen/Qwen3-VL-Embedding-2B")

        self.assertEqual(str(downloaded_snapshot.resolve()), result)
        download_mock.assert_called_once()
        self.assertEqual(project_root / ".models", Path(download_mock.call_args.kwargs["cache_dir"]))

    def test_ensure_project_local_model_snapshot_repairs_missing_stdio_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            downloaded_snapshot = (
                project_root
                / ".models"
                / "models--Qwen--Qwen3-VL-Embedding-2B"
                / "snapshots"
                / "downloaded123"
            )
            downloaded_snapshot.mkdir(parents=True)
            refs_main = downloaded_snapshot.parents[1] / "refs" / "main"
            refs_main.parent.mkdir(parents=True, exist_ok=True)
            refs_main.write_text("downloaded123", encoding="utf-8")

            original_stdout = sys.stdout
            original_stderr = sys.stderr
            try:
                sys.stdout = None
                sys.stderr = None
                with patch("memesort_worker.library.project_root", return_value=project_root):
                    with patch(
                        "memesort_worker.library._discover_snapshot_for_model_id",
                        side_effect=[None, None],
                    ):
                        with patch("huggingface_hub.snapshot_download", return_value=str(downloaded_snapshot)) as download_mock:
                            result = ensure_project_local_model_snapshot("Qwen/Qwen3-VL-Embedding-2B")
            finally:
                sys.stdout = original_stdout
                sys.stderr = original_stderr

        self.assertEqual(str(downloaded_snapshot.resolve()), result)
        download_mock.assert_called_once()

    def test_runtime_settings_default_to_cpu_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = get_runtime_settings(Path(temp_dir) / "library")

            self.assertEqual("cpu-low-memory", settings.selected_profile)
            self.assertEqual("qwen3-2b", settings.selected_model_key)
            self.assertEqual("qwen3-2b-cpu", settings.selected_recipe_preset)
            self.assertEqual(4, settings.gif_frame_count)
            self.assertEqual("qwen3-vl", settings.backend_name)

    def test_runtime_settings_can_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"

            save_runtime_settings(
                library_root,
                selected_profile="cuda-quality",
                selected_model_key="qwen3-2b",
                model_name_or_path="F:\\models\\Qwen3-VL-Embedding-2B",
            )
            settings = get_runtime_settings(library_root)

            self.assertEqual("cuda-quality", settings.selected_profile)
            self.assertEqual("qwen3-2b", settings.selected_model_key)
            self.assertEqual(
                "F:\\models\\Qwen3-VL-Embedding-2B",
                settings.model_name_or_path,
            )
            self.assertEqual("qwen3-2b-cuda-quality", settings.selected_recipe_preset)
            self.assertEqual("qwen3-vl", settings.backend_name)

    def test_runtime_settings_can_persist_backend_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"

            save_runtime_settings(
                library_root,
                selected_profile="cpu-low-memory",
                selected_model_key="qwen3-2b",
                model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                backend_name="qwen3-vl",
            )
            settings = get_runtime_settings(library_root)

            self.assertEqual("qwen3-vl", settings.backend_name)

    def test_runtime_settings_reject_mismatched_profile_and_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"

            with self.assertRaisesRegex(
                ValueError,
                "requires runtime profile cuda-quality",
            ):
                save_runtime_settings(
                    library_root,
                    selected_profile="cpu-low-memory",
                    selected_model_key="qwen3-2b",
                    model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                    selected_recipe_preset="qwen3-2b-cuda-quality",
                )

    def test_apply_runtime_selection_updates_settings_and_active_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            result = apply_runtime_selection(
                library_root,
                selected_profile="cuda-quality",
                selected_model_key="qwen3-8b",
                model_name_or_path="F:\\models\\Qwen3-VL-Embedding-8B",
                backend_name="qwen3-vl",
            )
            settings = get_runtime_settings(library_root)
            assets = list_assets(library_root)

        self.assertEqual("cuda-quality", settings.selected_profile)
        self.assertEqual("qwen3-8b", settings.selected_model_key)
        self.assertEqual("qwen3-8b-cuda-quality", settings.selected_recipe_preset)
        self.assertEqual("qwen3-vl", settings.backend_name)
        self.assertIn("Qwen3-VL-Embedding-8B / 4096d / cuda-quality", assets.active_recipe_label)
        self.assertGreaterEqual(result.reindex_jobs_created, 1)

    def test_runtime_config_for_profile_uses_profile_defaults(self) -> None:
        config = get_runtime_config_for_profile(
            "cuda-balanced",
            model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
        )

        self.assertEqual("cuda:0", config.device)
        self.assertEqual("float16", config.torch_dtype)
        self.assertEqual(8, config.num_threads)
        self.assertEqual(2, config.num_interop_threads)

    def test_default_library_root_prefers_appdata(self) -> None:
        with patch.dict("os.environ", {"APPDATA": r"C:\Users\tester\AppData\Roaming"}):
            result = default_library_root()

        self.assertEqual(
            Path(r"C:\Users\tester\AppData\Roaming\MemeSort"),
            result,
        )

    def test_resolve_preferred_port_returns_zero_when_busy(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy_socket:
            busy_socket.bind(("127.0.0.1", 0))
            busy_socket.listen(1)
            port = busy_socket.getsockname()[1]

            result = resolve_preferred_port("127.0.0.1", port)

        self.assertEqual(0, result)

    def test_launch_local_mvp_app_uses_default_root_and_opens_browser_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            expected_root = Path(temp_dir) / "MemeSort"

            with patch("memesort_worker.launcher.default_library_root", return_value=expected_root):
                with patch("memesort_worker.launcher.run_web_app") as run_web_app_mock:
                    launch_local_mvp_app(
                        library_root=None,
                        host="127.0.0.1",
                        preferred_port=8765,
                        open_browser=True,
                    )

        args = run_web_app_mock.call_args.args
        kwargs = run_web_app_mock.call_args.kwargs
        self.assertEqual(str(expected_root.resolve()), args[0])
        self.assertEqual("127.0.0.1", kwargs["host"])
        self.assertEqual(8765, kwargs["port"])
        self.assertIsNotNone(kwargs["on_started"])

    def test_launch_local_mvp_app_can_disable_browser_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("memesort_worker.launcher.run_web_app") as run_web_app_mock:
                launch_local_mvp_app(
                    library_root=temp_dir,
                    host="127.0.0.1",
                    preferred_port=8765,
                    open_browser=False,
                )

        kwargs = run_web_app_mock.call_args.kwargs
        self.assertIsNone(kwargs["on_started"])

    def test_runtime_health_check_reports_missing_model_path(self) -> None:
        with patch("memesort_worker.library.discover_local_model_path", return_value=None):
            with patch(
                "memesort_worker.library.ensure_project_local_model_snapshot",
                side_effect=RuntimeError("download failed"),
            ):
                result = run_runtime_health_check("cpu-low-memory", model_name_or_path=None)

        self.assertFalse(result.smoke_test_ok)
        self.assertIn("download failed", result.error)
        self.assertEqual("qwen3-2b", result.selected_model_key)
        self.assertGreaterEqual(len(result.diagnostic_steps), 3)

    def test_runtime_health_check_can_persist_last_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"

            with patch("memesort_worker.library.discover_local_model_path", return_value=None):
                with patch(
                    "memesort_worker.library.ensure_project_local_model_snapshot",
                    side_effect=RuntimeError("download failed"),
                ):
                    result = run_runtime_health_check(
                        "cpu-low-memory",
                        model_key="qwen3-2b",
                        model_name_or_path=None,
                        library_root=library_root,
                    )
            persisted = get_last_health_check(library_root)

        self.assertFalse(result.smoke_test_ok)
        self.assertIsNotNone(persisted)
        self.assertEqual("cpu-low-memory", persisted.profile_id)
        self.assertIn("download failed", persisted.error)
        self.assertGreaterEqual(len(persisted.diagnostic_steps), 3)

    def test_runtime_health_check_reports_structured_success_diagnostics(self) -> None:
        with patch("memesort_worker.library.discover_local_model_path", return_value=r"F:\git_repository\new_meme_sort\.models\Qwen2B"):
            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.embed_text.return_value = self._unit_vector(2048)
                backend.backend_id = "fake-qwen"
                result = run_runtime_health_check(
                    "cpu-low-memory",
                    model_key="qwen3-2b",
                    model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                )

        self.assertTrue(result.smoke_test_ok)
        self.assertEqual("Qwen3 2B", result.selected_model_label)
        self.assertEqual(2048, result.text_smoke_vector_dim)
        self.assertGreaterEqual(len(result.diagnostic_steps), 4)
        self.assertEqual("ok", result.diagnostic_steps[-1]["status"])

    def test_get_setup_state_reports_checklist_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            save_runtime_settings(
                library_root,
                selected_profile="cpu-low-memory",
                selected_model_key="qwen3-2b",
                model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                backend_name="qwen3-vl",
            )
            with patch(
                "memesort_worker.library.ensure_project_local_model_snapshot",
                return_value=r"F:\git_repository\new_meme_sort\.models\models--Qwen--Qwen3-VL-Embedding-2B\snapshots\abc123",
            ):
                with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                    backend = backend_factory.return_value
                    backend.embed_text.return_value = self._unit_vector(2048)
                    backend.backend_id = "fake-qwen"
                    run_runtime_health_check(
                        "cpu-low-memory",
                        model_key="qwen3-2b",
                        model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                        library_root=library_root,
                    )
            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            result = get_setup_state(library_root)

        self.assertTrue(result.runtime_profile_selected)
        self.assertTrue(result.embedding_model_selected)
        self.assertTrue(result.model_path_configured)
        self.assertTrue(result.health_check_has_run)
        self.assertTrue(result.assets_present)
        self.assertTrue(result.indexed_assets_present)
        self.assertEqual(6, len(result.checklist))
        self.assertGreaterEqual(len(result.runtime_readiness["last_health_diagnostic_steps"]), 1)
        self.assertEqual(2048, result.runtime_readiness["last_health_text_smoke_vector_dim"])
        self.assertFalse(result.runtime_readiness["last_health_model_downloaded"])

    def test_get_setup_state_surfaces_suggested_model_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"

            with patch(
                "memesort_worker.library.discover_local_model_path",
                return_value=r"C:\cache\Qwen3-VL-Embedding-2B",
            ):
                result = get_setup_state(library_root)

        self.assertEqual(r"C:\cache\Qwen3-VL-Embedding-2B", result.suggested_model_path)
        model_path_item = next(item for item in result.checklist if item["id"] == "model-path")
        self.assertEqual(r"C:\cache\Qwen3-VL-Embedding-2B", model_path_item["detail"])
        self.assertTrue(result.model_path_configured)
        self.assertEqual("Resolve model source", model_path_item["label"])

    def test_run_first_run_flow_persists_local_model_source_and_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            local_snapshot = (
                root
                / ".models"
                / "models--Qwen--Qwen3-VL-Embedding-2B"
                / "snapshots"
                / "abc123"
            )
            local_snapshot.mkdir(parents=True)

            with patch("memesort_worker.library.discover_local_model_path", return_value=None):
                with patch(
                    "memesort_worker.library.ensure_project_local_model_snapshot",
                    return_value=str(local_snapshot),
                ):
                    with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                        backend = backend_factory.return_value
                        backend.embed_text.return_value = self._unit_vector(2048)
                        backend.backend_id = "fake-qwen"
                        result = run_first_run_flow(
                            library_root,
                            selected_profile="cpu-low-memory",
                            selected_model_key="qwen3-2b",
                            model_name_or_path=None,
                            import_path=str(source_root),
                        )

            settings = get_runtime_settings(library_root)
            self.assertEqual(str(local_snapshot), settings.model_name_or_path)
            self.assertTrue(result.health_check["smoke_test_ok"])
            self.assertTrue(result.should_resume_worker_loop)
            self.assertIsNotNone(result.import_result)
            self.assertEqual(1, result.import_result["new_assets"])
            self.assertEqual("cpu-low-memory", result.runtime_selection["runtime_settings"]["selected_profile"])

    def test_build_app_state_returns_shell_neutral_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            controller = WorkerLoopController(library_root, interval_seconds=0.05)
            try:
                payload = build_app_state(library_root, worker_loop_snapshot=controller.snapshot()).to_dict()
            finally:
                controller.shutdown()

        self.assertEqual(str(library_root.resolve()), payload["library_root"])
        self.assertIn("runtime_profiles", payload)
        self.assertIn("model_variants", payload)
        self.assertIn("runtime_settings", payload)
        self.assertIn("setup_state", payload)
        self.assertIn("asset_summary", payload)
        self.assertIn("library_status", payload)
        self.assertIn("worker_loop", payload)

    def test_resolve_effective_model_source_prefers_configured_value(self) -> None:
        result = resolve_effective_model_source(
            "qwen3-2b",
            r"F:\models\Qwen3-VL-Embedding-2B",
        )

        self.assertEqual(r"F:\models\Qwen3-VL-Embedding-2B", result)

    def test_resolve_effective_model_source_prefers_local_snapshot_over_matching_hf_id(self) -> None:
        with patch(
            "memesort_worker.library.discover_local_model_path",
            return_value=r"C:\cache\Qwen3-VL-Embedding-2B",
        ):
            result = resolve_effective_model_source(
                "qwen3-2b",
                "Qwen/Qwen3-VL-Embedding-2B",
            )

        self.assertEqual(r"C:\cache\Qwen3-VL-Embedding-2B", result)

    def test_resolve_effective_model_source_can_reuse_global_cache_for_explicit_repo_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_home = Path(temp_dir)
            snapshot = (
                fake_home
                / ".cache"
                / "huggingface"
                / "hub"
                / "models--Qwen--Qwen3-VL-Embedding-2B"
                / "snapshots"
                / "abc123"
            )
            snapshot.mkdir(parents=True)
            refs_main = snapshot.parents[1] / "refs" / "main"
            refs_main.parent.mkdir(parents=True, exist_ok=True)
            refs_main.write_text("abc123", encoding="utf-8")

            with patch("memesort_worker.library.project_root", return_value=fake_home / "project"):
                with patch("memesort_worker.library.Path.home", return_value=fake_home):
                    result = resolve_effective_model_source(
                        "qwen3-8b",
                        "Qwen/Qwen3-VL-Embedding-2B",
                    )

        self.assertEqual(str(snapshot), result)

    def test_resolve_effective_model_source_downloads_into_project_store_when_allowed(self) -> None:
        with patch("memesort_worker.library.discover_local_model_path", return_value=None):
            with patch(
                "memesort_worker.library.ensure_project_local_model_snapshot",
                return_value=r"F:\git_repository\new_meme_sort\.models\models--Qwen--Qwen3-VL-Embedding-2B\snapshots\abc123",
            ) as download_mock:
                result = resolve_effective_model_source(
                    "qwen3-2b",
                    None,
                    allow_download=True,
                )

        self.assertEqual(
            r"F:\git_repository\new_meme_sort\.models\models--Qwen--Qwen3-VL-Embedding-2B\snapshots\abc123",
            result,
        )
        download_mock.assert_called_once_with("Qwen/Qwen3-VL-Embedding-2B")

    def test_resolve_effective_model_source_returns_none_when_no_local_model_is_ready(self) -> None:
        with patch(
            "memesort_worker.library.discover_local_model_path",
            return_value=None,
        ):
            result = resolve_effective_model_source(
                "qwen3-2b",
                None,
            )

        self.assertIsNone(result)

    def test_run_pending_jobs_without_embed_work_does_not_initialize_embedding_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                conn.execute("DELETE FROM job WHERE type = 'embed_asset'")
                conn.commit()
            finally:
                conn.close()

            with patch.dict(os.environ, {"MEMESORT_OCR_BACKEND": "debug"}):
                with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                    result = run_pending_jobs(
                        library_root,
                        backend_name="qwen3-vl",
                        model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                    )

        backend_factory.assert_not_called()
        self.assertEqual("qwen3-vl", result.backend)
        self.assertEqual(2, result.completed_jobs)

    def test_search_without_indexed_vectors_does_not_initialize_embedding_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                result = search_text(
                    library_root,
                    query="confused reaction",
                    backend_name="qwen3-vl",
                    model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                )

        backend_factory.assert_not_called()
        self.assertEqual([], result.results)

    def test_search_can_return_ocr_only_matches_without_embedding_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "hidden_needle.png", (255, 0, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            conn.row_factory = sqlite3.Row
            try:
                asset_id = conn.execute("SELECT id FROM asset").fetchone()["id"]
                ocr_recipe_id = library_module._ensure_default_ocr_recipe(conn)
                library_module._store_ocr_result(
                    conn,
                    asset_id,
                    ocr_recipe_id,
                    {
                        "engine": "test-ocr",
                        "texts": ["hidden needle"],
                        "scores": [0.99],
                        "boxes": [[]],
                        "text": "hidden needle",
                    },
                )
                conn.execute("DELETE FROM embedding_item")
                conn.commit()
            finally:
                conn.close()

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                result = search_text(
                    library_root,
                    query="hidden",
                    backend_name="qwen3-vl",
                    model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                )

        backend_factory.assert_not_called()
        self.assertEqual(1, len(result.results))
        self.assertEqual(["ocr"], result.results[0]["match_sources"])
        self.assertIn("hidden needle", result.results[0]["ocr_snippet"])

    def test_is_runtime_ready_for_indexing_requires_health_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            save_runtime_settings(
                library_root,
                selected_profile="cpu-low-memory",
                selected_model_key="qwen3-2b",
                model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                backend_name="qwen3-vl",
            )

            ready, message = is_runtime_ready_for_indexing(library_root)

        self.assertFalse(ready)
        self.assertIn("health check", message.lower())

    def test_get_asset_detail_includes_thumbnail_and_library_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            asset_id = list_assets(library_root).assets[0]["asset_id"]

            result = get_asset_detail(library_root, asset_id)

            self.assertEqual(asset_id, result.asset["asset_id"])
            self.assertTrue(result.asset["library_url"].startswith("/media/originals/"))
            self.assertTrue(result.asset["thumbnail_url"].startswith("/media/thumbnails/"))

    def test_remove_source_record_deletes_asset_when_last_source_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            image_path = source_root / "first.png"
            self._write_demo_image(image_path, (255, 0, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            asset = list_assets(library_root).assets[0]

            result = remove_source_record(
                library_root,
                asset_id=asset["asset_id"],
                source_path=str(image_path),
            )
            assets_after = list_assets(library_root)

        self.assertTrue(result.asset_deleted)
        self.assertEqual(0, len(assets_after.assets))

    def test_delete_asset_removes_asset_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            asset = list_assets(library_root).assets[0]

            result = delete_asset(library_root, asset_id=asset["asset_id"])
            assets_after = list_assets(library_root)

        self.assertTrue(result.asset_deleted)
        self.assertEqual(0, len(assets_after.assets))

    def test_delete_assets_removes_all_selected_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "one.png", (255, 0, 0))
            self._write_demo_image(source_root / "two.png", (0, 255, 0))
            import_folder(library_root, source_root)
            asset_ids = [asset["asset_id"] for asset in list_assets(library_root).assets]

            result = delete_assets(library_root, asset_ids)
            remaining_assets = len(list_assets(library_root).assets)

        self.assertEqual(asset_ids, result.affected_asset_ids)
        self.assertEqual(2, result.removed_source_records)
        self.assertEqual(0, remaining_assets)

    def test_rebuild_active_indexes_replaces_selected_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "one.png", (255, 0, 0))
            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")
            asset_id = list_assets(library_root).assets[0]["asset_id"]

            result = rebuild_active_indexes(library_root, [asset_id])
            pending_asset = list_assets(library_root).assets[0]
            run_pending_jobs(library_root, backend_name="debug")
            final_status = list_assets(library_root).assets[0]["status"]

        self.assertEqual([asset_id], result.affected_asset_ids)
        self.assertEqual(1, result.reindex_jobs_created)
        self.assertGreaterEqual(result.removed_embeddings, 1)
        self.assertIn(pending_asset["status"], {"pending_initial_index", "reindex_pending"})
        self.assertEqual("indexed", final_status)

    def test_retry_failed_jobs_requeues_failed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                conn.execute("UPDATE job SET status = 'failed', error_code = 'x', error_detail = 'y'")
                conn.commit()
            finally:
                conn.close()

            result = retry_failed_jobs(library_root)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                pending_count = conn.execute("SELECT COUNT(*) FROM job WHERE status = 'pending'").fetchone()[0]
            finally:
                conn.close()

        self.assertGreaterEqual(result.retried_jobs, 1)
        self.assertEqual(0, result.failed_jobs_remaining)
        self.assertGreaterEqual(pending_count, 1)

    def test_scan_duplicate_assets_returns_pairs_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            initialize_library(library_root)

            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                active_recipe_id = conn.execute(
                    "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state WHERE key = 'active_recipe_id'"
                ).fetchone()[0]
                now = "2026-05-22T00:00:00+00:00"
                for asset_id in ("asset-a", "asset-b", "asset-c"):
                    conn.execute(
                        """
                        INSERT INTO asset (
                            id, library_path, media_type, content_hash, byte_size, width, height,
                            imported_at, updated_at, deleted_at
                        ) VALUES (?, ?, 'image/png', ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (asset_id, f"originals/{asset_id}.png", f"hash-{asset_id}", 1, 24, 24, now, now),
                    )

                vectors = {
                    "asset-a": np.array([1.0, 0.0], dtype=np.float32),
                    "asset-b": np.array([0.96, 0.0], dtype=np.float32),
                    "asset-c": np.array([0.0, 1.0], dtype=np.float32),
                }
                for index, (asset_id, vector) in enumerate(vectors.items(), start=1):
                    normalized = vector / np.linalg.norm(vector)
                    conn.execute(
                        """
                        INSERT INTO embedding_item (
                            id, asset_id, recipe_id, kind, source_ref, vector_dim, vector_blob, created_at
                        ) VALUES (?, ?, ?, 'image', ?, ?, ?, ?)
                        """,
                        (
                            f"embedding-{index}",
                            asset_id,
                            active_recipe_id,
                            "original",
                            int(normalized.shape[0]),
                            sqlite3.Binary(normalized.astype(np.float32).tobytes()),
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

            result = scan_duplicate_assets(library_root, threshold=0.9)

            self.assertEqual(1, len(result.pairs))
            self.assertEqual("asset-a", result.pairs[0]["asset_a_id"])
            self.assertEqual("asset-b", result.pairs[0]["asset_b_id"])

    def test_get_library_status_summarizes_assets_and_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            status_before = get_library_status(library_root)
            self.assertEqual(1, status_before.total_assets)
            self.assertGreaterEqual(status_before.total_jobs, 2)
            self.assertIn("pending_initial_index", status_before.asset_counts)

            run_pending_jobs(library_root, backend_name="debug")
            status_after = get_library_status(library_root)
            self.assertIn("indexed", status_after.asset_counts)
            self.assertIn("completed", status_after.job_counts)

    def test_web_app_state_endpoint_returns_runtime_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            app = create_app(str(library_root))

            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            environ = {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/api/state",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": "0",
                "wsgi.input": BytesIO(b""),
            }
            body = b"".join(app(environ, start_response))
            payload = json.loads(body.decode("utf-8"))

            self.assertEqual("200 OK", captured["status"])
            self.assertIn("runtime_profiles", payload)
            self.assertIn("model_variants", payload)
            self.assertIn("asset_summary", payload)
            self.assertIn("setup_state", payload)
            self.assertEqual(str(library_root.resolve()), payload["library_root"])

    def test_web_app_media_endpoint_serves_thumbnail_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            environ = {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/media/thumbnails/" + next((library_root / "thumbnails").iterdir()).name,
                "QUERY_STRING": "",
                "CONTENT_LENGTH": "0",
                "wsgi.input": BytesIO(b""),
            }
            body = b"".join(app(environ, start_response))

            self.assertEqual("200 OK", captured["status"])
            self.assertGreater(len(body), 10)

    def test_web_app_library_status_endpoint_returns_job_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            import_folder(library_root, source_root)

            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            environ = {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/api/library-status",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": "0",
                "wsgi.input": BytesIO(b""),
            }
            body = b"".join(app(environ, start_response))
            payload = json.loads(body.decode("utf-8"))

            self.assertEqual("200 OK", captured["status"])
            self.assertIn("job_counts", payload)

    def test_web_app_search_endpoint_reads_query_string_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                environ = {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/api/search",
                    "QUERY_STRING": "query=red%20meme&top_k=5&backend_name=debug",
                    "CONTENT_LENGTH": "0",
                    "wsgi.input": BytesIO(b""),
                }
                body = b"".join(app(environ, start_response))
                payload = json.loads(body.decode("utf-8"))
            finally:
                app.shutdown()

            self.assertEqual("200 OK", captured["status"])
            self.assertEqual("red meme", payload["query"])
            self.assertGreaterEqual(len(payload["results"]), 1)

    def test_web_app_search_image_endpoint_reads_body_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            query_path = source_root / "first.png"
            self._write_demo_image(query_path, (255, 0, 0))
            self._write_demo_image(source_root / "second.png", (0, 255, 0))
            import_folder(library_root, source_root)
            run_pending_jobs(library_root, backend_name="debug")

            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                payload_json = json.dumps(
                    {
                        "path": str(query_path),
                        "top_k": 5,
                        "backend_name": "debug",
                    }
                ).encode("utf-8")
                environ = {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/api/search-image",
                    "QUERY_STRING": "",
                    "CONTENT_LENGTH": str(len(payload_json)),
                    "wsgi.input": BytesIO(payload_json),
                }
                body = b"".join(app(environ, start_response))
                payload = json.loads(body.decode("utf-8"))
            finally:
                app.shutdown()

            self.assertEqual("200 OK", captured["status"])
            self.assertEqual(str(query_path.resolve()), payload["query_path"])
            self.assertGreaterEqual(len(payload["results"]), 1)

    def test_web_app_pick_folder_endpoint_returns_selected_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                with patch("memesort_worker.webapp.pick_folder", return_value=r"F:\Pictures\memes"):
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "PATH_INFO": "/api/pick-folder",
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": str(len(b'{"title":"Pick","initial_path":"F:\\\\Pictures"}')),
                        "wsgi.input": BytesIO(b'{"title":"Pick","initial_path":"F:\\\\Pictures"}'),
                    }
                    body = b"".join(app(environ, start_response))
                    payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertEqual(r"F:\Pictures\memes", payload["selected_path"])
            finally:
                app.shutdown()

    def test_web_app_pick_file_endpoint_returns_selected_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                with patch("memesort_worker.webapp.pick_file", return_value=r"F:\Pictures\reaction.png"):
                    payload_json = json.dumps(
                        {
                            "title": "Pick",
                            "initial_path": r"F:\Pictures",
                            "filter_string": "Image Files|*.png",
                        }
                    ).encode("utf-8")
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "PATH_INFO": "/api/pick-file",
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": str(len(payload_json)),
                        "wsgi.input": BytesIO(payload_json),
                    }
                    body = b"".join(app(environ, start_response))
                    payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertEqual(r"F:\Pictures\reaction.png", payload["selected_path"])
            finally:
                app.shutdown()

    def test_web_app_reveal_managed_asset_file_endpoint_uses_library_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            import_folder(library_root, source_root)
            asset = list_assets(library_root).assets[0]
            expected_path = (library_root / str(asset["library_path"])).resolve()
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                with patch("memesort_worker.webapp.reveal_path_in_file_explorer") as reveal_mock:
                    payload_json = json.dumps(
                        {
                            "asset_id": asset["asset_id"],
                            "target": "managed",
                        }
                    ).encode("utf-8")
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "PATH_INFO": "/api/reveal-asset-file",
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": str(len(payload_json)),
                        "wsgi.input": BytesIO(payload_json),
                    }
                    body = b"".join(app(environ, start_response))
                    payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertEqual(str(expected_path), payload["revealed_path"])
                reveal_mock.assert_called_once_with(expected_path)
            finally:
                app.shutdown()

    def test_web_app_reveal_source_asset_file_endpoint_requires_source_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            source_path = source_root / "first.png"
            self._write_demo_image(source_path, (255, 0, 0))
            import_folder(library_root, source_root)
            asset = list_assets(library_root).assets[0]
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                with patch("memesort_worker.webapp.reveal_path_in_file_explorer") as reveal_mock:
                    payload_json = json.dumps(
                        {
                            "asset_id": asset["asset_id"],
                            "target": "source",
                            "source_path": str(source_path),
                        }
                    ).encode("utf-8")
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "PATH_INFO": "/api/reveal-asset-file",
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": str(len(payload_json)),
                        "wsgi.input": BytesIO(payload_json),
                    }
                    body = b"".join(app(environ, start_response))
                    payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertEqual(str(source_path.resolve()), payload["revealed_path"])
                reveal_mock.assert_called_once_with(source_path.resolve())
            finally:
                app.shutdown()

    def test_web_app_reveal_source_asset_file_endpoint_rejects_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))
            import_folder(library_root, source_root)
            asset = list_assets(library_root).assets[0]
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                with patch("memesort_worker.webapp.reveal_path_in_file_explorer") as reveal_mock:
                    payload_json = json.dumps(
                        {
                            "asset_id": asset["asset_id"],
                            "target": "source",
                            "source_path": str(source_root / "other.png"),
                        }
                    ).encode("utf-8")
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "PATH_INFO": "/api/reveal-asset-file",
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": str(len(payload_json)),
                        "wsgi.input": BytesIO(payload_json),
                    }
                    body = b"".join(app(environ, start_response))
                    payload = json.loads(body.decode("utf-8"))

                self.assertEqual("400 Bad Request", captured["status"])
                self.assertEqual("ValueError", payload["error"])
                reveal_mock.assert_not_called()
            finally:
                app.shutdown()

    def test_web_app_first_run_endpoint_runs_guided_flow_and_resumes_worker_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.embed_text.return_value = self._unit_vector(2048)
                backend.backend_id = "fake-qwen"
                with patch("memesort_worker.app_runtime.run_pending_jobs_for_active_runtime") as run_jobs_mock:
                    run_jobs_mock.return_value.to_dict.return_value = {
                        "backend": "fake-qwen",
                        "processed_jobs": 0,
                    }
                    app = create_app(str(library_root))
                    captured = {}

                    def start_response(status, headers):
                        captured["status"] = status
                        captured["headers"] = headers

                    try:
                        payload_json = json.dumps(
                            {
                                "selected_profile": "cpu-low-memory",
                                "selected_model_key": "qwen3-2b",
                                "model_name_or_path": "Qwen/Qwen3-VL-Embedding-2B",
                                "import_path": str(source_root),
                                "gif_frame_count": 4,
                                "backend_name": "qwen3-vl",
                            }
                        ).encode("utf-8")
                        environ = {
                            "REQUEST_METHOD": "POST",
                            "PATH_INFO": "/api/first-run",
                            "QUERY_STRING": "",
                            "CONTENT_LENGTH": str(len(payload_json)),
                            "wsgi.input": BytesIO(payload_json),
                        }
                        body = b"".join(app(environ, start_response))
                        payload = json.loads(body.decode("utf-8"))

                        self.assertEqual("200 OK", captured["status"])
                        self.assertTrue(payload["health_check"]["smoke_test_ok"])
                        self.assertEqual(1, payload["import_result"]["new_assets"])
                        self.assertFalse(payload["worker_loop"]["paused"])
                    finally:
                        app.shutdown()

    def test_web_app_import_and_start_index_resumes_worker_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            save_runtime_settings(
                library_root,
                selected_profile="cpu-low-memory",
                selected_model_key="qwen3-2b",
                model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                backend_name="qwen3-vl",
            )
            with patch("memesort_worker.library.get_embedding_backend") as backend_factory:
                backend = backend_factory.return_value
                backend.embed_text.return_value = self._unit_vector(2048)
                backend.backend_id = "fake-qwen"
                run_runtime_health_check(
                    "cpu-low-memory",
                    model_key="qwen3-2b",
                    model_name_or_path="Qwen/Qwen3-VL-Embedding-2B",
                    library_root=library_root,
                )

            with patch("memesort_worker.app_runtime.run_pending_jobs_for_active_runtime") as run_jobs_mock:
                run_jobs_mock.return_value.to_dict.return_value = {
                    "backend": "fake-qwen",
                    "processed_jobs": 0,
                }
                app = create_app(str(library_root))
                captured = {}

                def start_response(status, headers):
                    captured["status"] = status
                    captured["headers"] = headers

                try:
                    payload_json = json.dumps({"path": str(source_root)}).encode("utf-8")
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "PATH_INFO": "/api/import-and-start-index",
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": str(len(payload_json)),
                        "wsgi.input": BytesIO(payload_json),
                    }
                    body = b"".join(app(environ, start_response))
                    payload = json.loads(body.decode("utf-8"))

                    self.assertEqual("200 OK", captured["status"])
                    self.assertEqual(1, payload["import_result"]["new_assets"])
                    self.assertFalse(payload["worker_loop"]["paused"])
                finally:
                    app.shutdown()

    def test_web_app_import_and_start_index_rejects_unready_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                payload_json = json.dumps({"path": str(source_root)}).encode("utf-8")
                environ = {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/api/import-and-start-index",
                    "QUERY_STRING": "",
                    "CONTENT_LENGTH": str(len(payload_json)),
                    "wsgi.input": BytesIO(payload_json),
                }
                body = b"".join(app(environ, start_response))
                payload = json.loads(body.decode("utf-8"))

                self.assertEqual("400 Bad Request", captured["status"])
                self.assertIn("health check", payload["detail"].lower())
            finally:
                app.shutdown()

    def test_web_app_remove_source_record_endpoint_deletes_orphan_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            image_path = source_root / "first.png"
            self._write_demo_image(image_path, (255, 0, 0))

            import_folder(library_root, source_root)
            asset_id = list_assets(library_root).assets[0]["asset_id"]
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                payload_json = json.dumps(
                    {
                        "asset_id": asset_id,
                        "source_path": str(image_path),
                    }
                ).encode("utf-8")
                environ = {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/api/remove-source-record",
                    "QUERY_STRING": "",
                    "CONTENT_LENGTH": str(len(payload_json)),
                    "wsgi.input": BytesIO(payload_json),
                }
                body = b"".join(app(environ, start_response))
                payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertTrue(payload["asset_deleted"])
                self.assertEqual(0, len(list_assets(library_root).assets))
            finally:
                app.shutdown()

    def test_web_app_delete_asset_endpoint_removes_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            asset_id = list_assets(library_root).assets[0]["asset_id"]
            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                payload_json = json.dumps({"asset_id": asset_id}).encode("utf-8")
                environ = {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/api/delete-asset",
                    "QUERY_STRING": "",
                    "CONTENT_LENGTH": str(len(payload_json)),
                    "wsgi.input": BytesIO(payload_json),
                }
                body = b"".join(app(environ, start_response))
                payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertTrue(payload["asset_deleted"])
                self.assertEqual(0, len(list_assets(library_root).assets))
            finally:
                app.shutdown()

    def test_web_app_retry_failed_jobs_endpoint_requeues_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source_root = root / "source"
            source_root.mkdir()
            self._write_demo_image(source_root / "first.png", (255, 0, 0))

            import_folder(library_root, source_root)
            conn = sqlite3.connect(library_root / DATABASE_NAME)
            try:
                conn.execute("UPDATE job SET status = 'failed', error_code = 'x', error_detail = 'y'")
                conn.commit()
            finally:
                conn.close()

            app = create_app(str(library_root))
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            try:
                environ = {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/api/retry-failed-jobs",
                    "QUERY_STRING": "",
                    "CONTENT_LENGTH": "2",
                    "wsgi.input": BytesIO(b"{}"),
                }
                body = b"".join(app(environ, start_response))
                payload = json.loads(body.decode("utf-8"))

                self.assertEqual("200 OK", captured["status"])
                self.assertGreaterEqual(payload["retried_jobs"], 1)
                self.assertEqual(0, payload["failed_jobs_remaining"])
            finally:
                app.shutdown()

    def test_worker_loop_controller_can_pause_resume_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = WorkerLoopController(Path(temp_dir), interval_seconds=0.05)
            try:
                snapshot = controller.snapshot()
                self.assertTrue(snapshot.running)
                self.assertTrue(snapshot.paused)
                self.assertTrue(snapshot.event_log_path.endswith("worker-loop.jsonl"))

                controller.resume()
                time.sleep(0.01)
                resumed = controller.snapshot()
                self.assertFalse(resumed.paused)
                self.assertGreaterEqual(len(resumed.persisted_events), 1)

                controller.pause()
                paused = controller.snapshot()
                self.assertTrue(paused.paused)
            finally:
                controller.shutdown()

    def test_worker_loop_persists_events_to_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = WorkerLoopController(Path(temp_dir), interval_seconds=0.05)
            try:
                controller.resume()
                time.sleep(0.02)
                controller.pause()
                snapshot = controller.snapshot()
                event_log_path = Path(snapshot.event_log_path)
                self.assertTrue(event_log_path.exists())
                lines = event_log_path.read_text(encoding="utf-8").strip().splitlines()
                self.assertGreaterEqual(len(lines), 3)
                payload = json.loads(lines[-1])
                self.assertIn(payload["event"], {"worker-loop-paused", "tick-started", "tick-finished", "tick-failed"})
            finally:
                controller.shutdown()

    def test_web_app_worker_loop_endpoints_control_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            app = create_app(str(library_root))
            try:
                def call(path, method="POST"):
                    captured = {}

                    def start_response(status, headers):
                        captured["status"] = status
                        captured["headers"] = headers

                    environ = {
                        "REQUEST_METHOD": method,
                        "PATH_INFO": path,
                        "QUERY_STRING": "",
                        "CONTENT_LENGTH": "2" if method == "POST" else "0",
                        "wsgi.input": BytesIO(b"{}" if method == "POST" else b""),
                    }
                    body = b"".join(app(environ, start_response))
                    return captured["status"], json.loads(body.decode("utf-8"))

                status, payload = call("/api/worker-loop", method="GET")
                self.assertEqual("200 OK", status)
                self.assertTrue(payload["paused"])

                status, payload = call("/api/worker-loop/resume")
                self.assertEqual("200 OK", status)
                self.assertFalse(payload["paused"])

                status, payload = call("/api/worker-loop/pause")
                self.assertEqual("200 OK", status)
                self.assertTrue(payload["paused"])
            finally:
                app.shutdown()

    def _write_demo_image(
        self,
        path: Path,
        rgb: tuple[int, int, int],
        size: tuple[int, int] = (24, 24),
    ) -> None:
        from PIL import Image

        image = Image.new("RGB", size, rgb)
        image.save(path)

    def _write_demo_gif(
        self,
        path: Path,
        size: tuple[int, int] = (24, 24),
    ) -> None:
        from PIL import Image

        frames = [
            Image.new("RGB", size, color)
            for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
        ]
        frames[0].save(
            path,
            save_all=True,
            append_images=frames[1:],
            format="GIF",
            duration=80,
            loop=0,
        )

    def _unit_vector(self, dimension: int) -> np.ndarray:
        vector = np.zeros(dimension, dtype=np.float32)
        vector[0] = 1.0
        return vector

    def _fake_vector_row(
        self,
        asset_id: str,
        vector: np.ndarray,
        library_path: str,
    ) -> sqlite3.Row:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE fake (
                asset_id TEXT,
                vector_dim INTEGER,
                vector_blob BLOB,
                library_path TEXT,
                media_type TEXT,
                content_hash TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fake (asset_id, vector_dim, vector_blob, library_path, media_type, content_hash)
            VALUES (?, ?, ?, ?, 'image/png', ?)
            """,
            (
                asset_id,
                int(vector.shape[0]),
                sqlite3.Binary(vector.astype(np.float32).tobytes()),
                library_path,
                f"hash-{asset_id}-{library_path}",
            ),
        )
        row = conn.execute("SELECT * FROM fake").fetchone()
        conn.close()
        return row


if __name__ == "__main__":
    unittest.main()
