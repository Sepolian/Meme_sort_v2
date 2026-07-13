from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from memesort_worker.embedding_backend import (
    EmbeddingRuntimeConfig,
    LlamaCppEmbeddingBackend,
)
from memesort_worker.library import (
    RuntimeHealthResult,
    get_runtime_settings,
    is_runtime_ready_for_indexing,
    list_runtime_profiles,
    resolve_recipe_preset,
    save_runtime_settings,
)
from memesort_worker.llama_cpp_backend import (
    LlamaCppBackendError,
    LlamaCppEmbeddingAdapter,
    LlamaCppServer,
    LlamaCppServerConfig,
    MEDIA_MARKER,
    resolve_gguf_bundle,
    verify_qwen3_vl_embedding_2b_bundle,
)
from memesort_worker.runtime_service import run_runtime_health_check


class LlamaCppBackendTests(unittest.TestCase):
    def _write_bundle(self, root: Path) -> tuple[Path, Path]:
        main_model = root / "Qwen3-VL-Embedding-2B.Q4_K_M.gguf"
        mmproj = root / "mmproj-Qwen3-VL-Embedding-2B.f16.gguf"
        main_model.write_bytes(b"gguf-main")
        mmproj.write_bytes(b"gguf-mmproj")
        return main_model, mmproj

    def test_resolve_gguf_bundle_finds_main_and_mmproj(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected_main, expected_mmproj = self._write_bundle(root)

            main_model, mmproj = resolve_gguf_bundle(str(root))

        self.assertEqual(expected_main, main_model)
        self.assertEqual(expected_mmproj, mmproj)

    def test_resolve_gguf_bundle_rejects_missing_mmproj(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            main_model = Path(temp_dir) / "model.Q4_K_M.gguf"
            main_model.write_bytes(b"gguf-main")

            with self.assertRaisesRegex(LlamaCppBackendError, "mmproj"):
                resolve_gguf_bundle(str(main_model))

    def test_verified_recipe_rejects_different_gguf_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            main_model, mmproj = self._write_bundle(Path(temp_dir))

            with self.assertRaisesRegex(LlamaCppBackendError, "Unexpected SHA256"):
                verify_qwen3_vl_embedding_2b_bundle(main_model, mmproj)

    def test_adapter_sends_multimodal_embedding_payload(self) -> None:
        adapter = LlamaCppEmbeddingAdapter(
            LlamaCppServerConfig(model_path="unused", server_url="http://127.0.0.1:8080")
        )
        expected = np.array([1.0, 0.0], dtype=np.float32)
        with patch.object(adapter.server, "request_embedding", return_value=expected) as request:
            result = adapter.embed_image_bytes(b"image", instruction="Retrieve images")

        self.assertIs(result, expected)
        request_input = request.call_args.args[0]
        self.assertEqual(1, len(request_input))
        self.assertIn(MEDIA_MARKER, request_input[0]["prompt_string"])
        self.assertEqual(["aW1hZ2U="], request_input[0]["multimodal_data"])

    def test_server_parses_openai_embedding_response(self) -> None:
        server = LlamaCppServer(
            LlamaCppServerConfig(model_path="unused", server_url="http://127.0.0.1:8080")
        )
        with patch.object(
            server,
            "_request_json",
            side_effect=[
                {"status": "ok"},
                {"data": [{"embedding": [0.25, 0.75]}]},
            ],
        ):
            vector = server.request_embedding("hello")

        np.testing.assert_allclose(np.array([0.25, 0.75], dtype=np.float32), vector)

    def test_server_rejects_remote_url_to_protect_local_images(self) -> None:
        with self.assertRaisesRegex(LlamaCppBackendError, "loopback-only"):
            LlamaCppServer(
                LlamaCppServerConfig(
                    model_path="unused",
                    server_url="https://example.com",
                )
            )

    def test_embedding_backend_truncates_and_renormalizes_mrl_dimension(self) -> None:
        config = EmbeddingRuntimeConfig(
            model_name_or_path="bundle",
            llama_server_url="http://127.0.0.1:8080",
        )
        with patch(
            "memesort_worker.llama_cpp_backend.LlamaCppEmbeddingAdapter.embed_text",
            return_value=np.array([3.0, 4.0, 12.0], dtype=np.float32),
        ):
            backend = LlamaCppEmbeddingBackend(config)
            vector = backend.embed_text("hello", output_dimension=2)

        np.testing.assert_allclose(np.array([0.6, 0.8], dtype=np.float32), vector)

    def test_vulkan_profile_selects_llama_cpp_and_distinct_recipe(self) -> None:
        profiles = {profile.profile_id: profile for profile in list_runtime_profiles()}

        self.assertEqual("llama.cpp", profiles["vulkan-balanced"].backend_name)
        self.assertEqual(
            "qwen3-2b-vulkan-balanced",
            resolve_recipe_preset("vulkan-balanced", "qwen3-2b"),
        )

    def test_runtime_settings_infer_backend_from_vulkan_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            save_runtime_settings(
                library_root,
                selected_profile="vulkan-balanced",
                selected_model_key="qwen3-2b",
                model_name_or_path=None,
            )
            settings = get_runtime_settings(library_root)

        self.assertEqual("llama.cpp", settings.backend_name)
        self.assertEqual("qwen3-2b-vulkan-balanced", settings.selected_recipe_preset)

    def test_old_transformers_health_check_cannot_authorize_vulkan_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            bundle = Path(temp_dir) / "bundle"
            bundle.mkdir()
            self._write_bundle(bundle)
            save_runtime_settings(
                library_root,
                selected_profile="vulkan-balanced",
                selected_model_key="qwen3-2b",
                model_name_or_path=str(bundle),
            )
            stale = RuntimeHealthResult(
                profile_id="cpu-low-memory",
                backend_name="qwen3-vl",
                model_name_or_path=str(bundle),
                selected_model_key="qwen3-2b",
                selected_model_label="Qwen3 2B",
                device="cpu",
                torch_dtype="auto",
                torch_available=True,
                cuda_available=False,
                gpu_name=None,
                model_source_origin="explicit-local-path",
                model_downloaded=False,
                text_smoke_vector_dim=2048,
                diagnostic_steps=[],
                smoke_test_ok=True,
                error=None,
            )
            with patch(
                "memesort_worker.library.get_last_health_check",
                return_value=stale,
            ):
                ready, detail = is_runtime_ready_for_indexing(library_root)

        self.assertFalse(ready)
        self.assertIn("stale", detail.lower())

    def test_vulkan_health_check_does_not_download_missing_gguf(self) -> None:
        with patch(
            "memesort_worker.library.ensure_project_local_model_snapshot"
        ) as download, patch(
            "memesort_worker.library.discover_local_gguf_model_path",
            return_value=None,
        ):
            result = run_runtime_health_check(
                "vulkan-balanced",
                model_key="qwen3-2b",
                model_name_or_path=None,
            )

        self.assertFalse(result.smoke_test_ok)
        self.assertEqual("llama.cpp", result.backend_name)
        self.assertIn("GGUF", result.error)
        download.assert_not_called()

    def test_vulkan_health_check_validates_text_and_image_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = Path(temp_dir)
            self._write_bundle(bundle)
            with patch(
                "memesort_worker.llama_cpp_backend.discover_llama_server",
                return_value=Path("llama-server.exe"),
            ):
                with patch(
                    "memesort_worker.llama_cpp_backend.probe_llama_devices",
                    return_value="Vulkan0: Test GPU",
                ):
                    with patch(
                        "memesort_worker.llama_cpp_backend.verify_qwen3_vl_embedding_2b_bundle"
                    ):
                        with patch("memesort_worker.library.get_embedding_backend") as factory:
                            backend = factory.return_value
                            backend.embed_text.return_value = np.ones(2048, dtype=np.float32)
                            backend.embed_image_bytes.return_value = np.ones(2048, dtype=np.float32)
                            result = run_runtime_health_check(
                                "vulkan-balanced",
                                model_key="qwen3-2b",
                                model_name_or_path=str(bundle),
                            )

        self.assertTrue(result.smoke_test_ok)
        self.assertEqual("Vulkan0: Test GPU", result.gpu_name)
        self.assertEqual("image-embedding-smoke", result.diagnostic_steps[-1]["step"])
        backend.embed_text.assert_called_once()
        backend.embed_image_bytes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
