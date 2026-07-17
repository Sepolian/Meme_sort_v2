from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from memesort_worker.embedding_backend import (
    EmbeddingBackendError,
    EmbeddingRuntimeConfig,
    LlamaCppEmbeddingBackend,
)
from memesort_worker import library as library_module
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
    load_server_config,
    resolve_gguf_bundle,
    verify_qwen3_vl_embedding_2b_bundle,
)
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.runtime_admission import VulkanDeviceInfo
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
        config = load_server_config()
        adapter = LlamaCppEmbeddingAdapter(config)
        expected = np.array([1.0, 0.0], dtype=np.float32)
        with patch.object(adapter.server, "request_embedding", return_value=expected) as request:
            result = adapter.embed_image_bytes(b"image", instruction="Retrieve images")

        self.assertIs(result, expected)
        request_input = request.call_args.args[0]
        self.assertEqual(1, len(request_input))
        self.assertIn(config.media_marker, request_input[0]["prompt_string"])
        self.assertEqual(["aW1hZ2U="], request_input[0]["multimodal_data"])

    def test_server_parses_openai_embedding_response(self) -> None:
        server = LlamaCppServer(load_server_config())
        server._base_url = "http://127.0.0.1:8080"
        with patch.object(server, "_ensure_ready"), patch.object(
            server, "_request_json", return_value={"data": [{"embedding": [0.25, 0.75]}]}
        ) as request:
            vector = server.request_embedding("hello")

        np.testing.assert_allclose(np.array([0.25, 0.75], dtype=np.float32), vector)
        self.assertEqual(
            load_runtime_manifest().model.request_model,
            request.call_args.kwargs["payload"]["model"],
        )

    def test_server_config_is_fully_derived_from_manifest(self) -> None:
        manifest = load_runtime_manifest()
        config = load_server_config()

        self.assertEqual(manifest.llama_server_path, config.executable_path)
        self.assertEqual(manifest.main_model_path, config.model_path)
        self.assertEqual(manifest.projector_path, config.mmproj_path)
        self.assertEqual("Vulkan0", config.device)
        self.assertEqual(manifest.llama_cpp.server.parallel_slots, config.parallel_slots)
        self.assertEqual(manifest.model.request_model, config.request_model)

    def test_embedding_backend_rejects_dimension_mismatch(self) -> None:
        config = EmbeddingRuntimeConfig()
        with patch(
            "memesort_worker.llama_cpp_backend.LlamaCppEmbeddingAdapter.embed_text",
            return_value=np.array([3.0, 4.0, 12.0], dtype=np.float32),
        ):
            backend = LlamaCppEmbeddingBackend(config)
            with self.assertRaisesRegex(EmbeddingBackendError, "expected exactly 2"):
                backend.embed_text("hello", output_dimension=2)

    def test_embedding_backend_returns_normalized_fp32(self) -> None:
        with patch(
            "memesort_worker.llama_cpp_backend.LlamaCppEmbeddingAdapter.embed_text",
            return_value=np.array([3.0, 4.0], dtype=np.float64),
        ):
            vector = LlamaCppEmbeddingBackend(EmbeddingRuntimeConfig()).embed_text(
                "hello", output_dimension=2
            )

        self.assertEqual(np.dtype(np.float32), vector.dtype)
        np.testing.assert_allclose(np.array([0.6, 0.8], dtype=np.float32), vector)
        self.assertAlmostEqual(1.0, float(np.linalg.norm(vector)), places=6)

    def test_embedding_backend_rejects_zero_and_non_finite_vectors(self) -> None:
        backend = LlamaCppEmbeddingBackend(EmbeddingRuntimeConfig())
        for vector, message in (
            (np.zeros(2, dtype=np.float32), "zero vector"),
            (np.array([np.nan, 1.0], dtype=np.float32), "NaN or infinite"),
            (np.array([np.inf, 1.0], dtype=np.float32), "NaN or infinite"),
        ):
            with self.subTest(vector=vector), patch.object(
                backend._adapter, "embed_text", return_value=vector
            ):
                with self.assertRaisesRegex(EmbeddingBackendError, message):
                    backend.embed_text("hello", output_dimension=2)

    def test_managed_server_command_uses_manifest_vulkan_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "llama-server.exe"
            model = root / "model.gguf"
            projector = root / "mmproj.gguf"
            for path in (executable, model, projector):
                path.write_bytes(b"test")
            config = replace(
                load_server_config(),
                executable_path=executable,
                model_path=model,
                mmproj_path=projector,
            )
            server = LlamaCppServer(config)
            with patch(
                "memesort_worker.llama_cpp_backend.subprocess.Popen"
            ) as popen, patch(
                "memesort_worker.llama_cpp_backend._validate_manifest_runtime"
            ), patch.object(server, "_wait_until_healthy"):
                popen.return_value.poll.return_value = None
                _ = server.base_url

            command = popen.call_args.args[0]
            self.assertEqual("Vulkan0", command[command.index("--device") + 1])
            self.assertEqual(
                str(config.parallel_slots), command[command.index("--parallel") + 1]
            )
            self.assertEqual(config.pooling, command[command.index("--pooling") + 1])
            self.assertEqual("2", command[command.index("--embd-normalize") + 1])
            server.close()

    def test_vulkan_profile_selects_llama_cpp_and_distinct_recipe(self) -> None:
        profiles = {profile.profile_id: profile for profile in list_runtime_profiles()}

        self.assertEqual("llama.cpp", profiles["vulkan-balanced"].backend_name)
        self.assertEqual(
            "qwen3-2b-vulkan-balanced",
            resolve_recipe_preset("vulkan-balanced", "qwen3-2b"),
        )

    def test_vulkan_recipe_identity_is_derived_from_manifest(self) -> None:
        manifest = load_runtime_manifest()
        recipe = library_module.RECIPE_PRESETS["qwen3-2b-vulkan-balanced"]

        self.assertEqual(manifest.model.id, recipe["model_id"])
        self.assertEqual(manifest.recipe_fingerprint, recipe["model_revision"])
        self.assertEqual(manifest.model.output_dimension, recipe["output_dimension"])
        self.assertEqual(manifest.preprocessing.version, recipe["preprocess_version"])
        self.assertEqual(manifest.embedding.instruction_id, recipe["instruction_key"])

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
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_manifest = replace(
                load_runtime_manifest(),
                source_path=Path(temp_dir) / "runtime-manifest.json",
            )
            with patch(
                "memesort_worker.library.ensure_project_local_model_snapshot"
            ) as download, patch(
                "memesort_worker.runtime_service.load_runtime_manifest",
                return_value=missing_manifest,
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
                        "memesort_worker.runtime_service.probe_vulkan0",
                        return_value=VulkanDeviceInfo(
                            index=0,
                            vendor_id=0x1002,
                            vendor_name="amd",
                            device_id=1,
                            device_name="Test GPU",
                        ),
                    ):
                        with patch(
                            "memesort_worker.runtime_service.validate_runtime_activation"
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
