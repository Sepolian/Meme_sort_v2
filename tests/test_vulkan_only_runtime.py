from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memesort_worker.active_runtime import search_text_for_active_runtime
from memesort_worker.embedding_backend import EmbeddingBackendError, get_embedding_backend
from memesort_worker.library import (
    get_runtime_settings,
    initialize_library,
    list_assets,
    list_model_variants,
    list_runtime_profiles,
    save_runtime_settings,
)
from memesort_worker.runtime_manifest import load_runtime_manifest


class VulkanOnlyRuntimeTests(unittest.TestCase):
    def test_only_manifest_vulkan_runtime_is_exposed(self) -> None:
        manifest = load_runtime_manifest()
        profiles = list_runtime_profiles()
        models = list_model_variants()

        self.assertEqual(["vulkan"], [profile.profile_id for profile in profiles])
        self.assertEqual(["manifest"], [model.model_key for model in models])
        self.assertEqual("llama.cpp", profiles[0].backend_name)
        self.assertEqual("Vulkan0", profiles[0].device)
        self.assertEqual(manifest.model.id, models[0].model_id)
        self.assertEqual(manifest.model.output_dimension, models[0].output_dimension)

    def test_new_library_uses_manifest_recipe_and_canonical_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            initialize_library(root)
            settings = get_runtime_settings(root)
            assets = list_assets(root)

        self.assertEqual("vulkan", settings.selected_profile)
        self.assertEqual("manifest", settings.selected_model_key)
        self.assertEqual("vulkan-manifest", settings.selected_recipe_preset)
        self.assertEqual("llama.cpp", settings.backend_name)
        self.assertIsNone(settings.model_name_or_path)
        self.assertIn(" / vulkan", assets.active_recipe_label)

    def test_custom_model_path_and_legacy_backend_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            with self.assertRaisesRegex(ValueError, "owned by runtime-manifest"):
                save_runtime_settings(
                    root,
                    selected_profile="vulkan",
                    selected_model_key="manifest",
                    model_name_or_path=r"C:\custom\model.gguf",
                )
            with self.assertRaisesRegex(ValueError, "Unknown runtime profile"):
                save_runtime_settings(
                    root,
                    selected_profile="cpu-low-memory",
                    selected_model_key="manifest",
                    model_name_or_path=None,
                )

    def test_request_cannot_override_active_vulkan_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "memesort_worker.active_runtime._search_text"
        ) as search:
            with self.assertRaisesRegex(ValueError, "Backend override"):
                search_text_for_active_runtime(
                    Path(temp_dir) / "library",
                    query="test",
                    top_k=3,
                    backend_name="debug",
                )
        search.assert_not_called()

    def test_legacy_embedding_backends_cannot_be_constructed(self) -> None:
        for backend_name in ("debug", "qwen3-vl", "cpu", "cuda"):
            with self.subTest(backend_name=backend_name):
                with self.assertRaisesRegex(EmbeddingBackendError, "Vulkan-only"):
                    get_embedding_backend(backend_name)


if __name__ == "__main__":
    unittest.main()
