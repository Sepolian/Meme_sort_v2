from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from memesort_worker.runtime_manifest import (
    RuntimeManifestError,
    default_manifest_path,
    load_runtime_manifest,
)


class RuntimeManifestTests(unittest.TestCase):
    def _raw_manifest(self) -> dict[str, object]:
        return json.loads(default_manifest_path().read_text(encoding="utf-8"))

    def _load_raw(self, raw: dict[str, object]):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime-manifest.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return load_runtime_manifest(path)

    def test_checked_in_manifest_is_valid_and_resolves_project_paths(self) -> None:
        manifest = load_runtime_manifest()

        self.assertEqual(1, manifest.schema_version)
        self.assertEqual("Vulkan0", manifest.platform.device)
        self.assertEqual(768, manifest.model.output_dimension)
        self.assertEqual("float32", manifest.embedding.storage_dtype)
        self.assertEqual("unsloth/embeddinggemma-2-GGUF:Q8_0", manifest.model.id)
        self.assertEqual("google/embeddinggemma-2", manifest.model.base_model_id)
        self.assertEqual("b11457", manifest.llama_cpp.build)
        self.assertEqual(33377746, manifest.llama_cpp.archive.size_bytes)
        self.assertEqual(
            "d01301582c711a69b9747b5984710d6ca99e57d95f680d3d33753cec570b4cb6",
            manifest.llama_cpp.archive.sha256,
        )
        self.assertEqual(309855520, manifest.model.main.size_bytes)
        self.assertEqual(554821120, manifest.model.projector.size_bytes)
        self.assertEqual(
            "6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc",
            manifest.model.main.sha256,
        )
        self.assertEqual(
            "90e7b0238009e2954f856f2081dcf7f35af026b64c765e98f4777053e1754460",
            manifest.model.projector.sha256,
        )
        self.assertEqual("task: search result | query: ", manifest.embedding.instruction)
        self.assertEqual("image-only", manifest.embedding.image_input_policy)
        self.assertEqual("mean", manifest.embedding.pooling)
        self.assertEqual(2048, manifest.llama_cpp.server.context_size)
        self.assertEqual(2048, manifest.llama_cpp.server.batch_size)
        self.assertEqual(2048, manifest.llama_cpp.server.ubatch_size)
        self.assertEqual("off", manifest.llama_cpp.server.flash_attention)
        self.assertTrue(manifest.llama_cpp.server.vulkan_disable_f16)
        self.assertEqual(
            manifest.project_root / ".runtime" / "llama.cpp-b11457-vulkan" / "llama-server.exe",
            manifest.llama_server_path,
        )
        self.assertEqual(71, len(manifest.recipe_id))
        self.assertTrue(manifest.recipe_display_id.startswith("vulkan-"))

    def test_portable_data_root_rehomes_only_runtime_and_model_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portable_data_root = (Path(temp_dir) / "MemeSortData").resolve()
            manifest = load_runtime_manifest(portable_data_root=portable_data_root)

            self.assertEqual(
                manifest.download_dir,
                portable_data_root / "runtime" / "downloads",
            )
            self.assertEqual(
                manifest.llama_install_dir,
                portable_data_root / "runtime" / "llama.cpp-b11457-vulkan",
            )
            self.assertEqual(
                manifest.model_install_dir,
                portable_data_root / "models" / "gguf" / "embeddinggemma-2-q8_0",
            )
            self.assertEqual(
                manifest.activation_record_path,
                portable_data_root / "runtime" / "active-runtime.json",
            )

    def test_unknown_fields_fail_fast(self) -> None:
        raw = self._raw_manifest()
        raw["surprise"] = True

        with self.assertRaisesRegex(RuntimeManifestError, "unknown surprise"):
            self._load_raw(raw)

    def test_model_dimension_must_be_positive_integer(self) -> None:
        raw = self._raw_manifest()
        model = raw["model"]
        assert isinstance(model, dict)
        model["output_dimension"] = 0

        with self.assertRaisesRegex(RuntimeManifestError, "model.output_dimension"):
            self._load_raw(raw)

    def test_runtime_only_changes_do_not_change_recipe_fingerprint(self) -> None:
        raw = self._raw_manifest()
        modified = copy.deepcopy(raw)
        llama_cpp = modified["llama_cpp"]
        assert isinstance(llama_cpp, dict)
        server = llama_cpp["server"]
        assert isinstance(server, dict)
        server["parallel_slots"] = 3
        server["startup_timeout_seconds"] = 999
        logging = modified["logging"]
        assert isinstance(logging, dict)
        logging["file_count"] = 9

        self.assertEqual(
            self._load_raw(raw).recipe_fingerprint,
            self._load_raw(modified).recipe_fingerprint,
        )

    def test_pinned_artifact_change_changes_runtime_fingerprint(self) -> None:
        raw = self._raw_manifest()
        modified = copy.deepcopy(raw)
        llama_cpp = modified["llama_cpp"]
        assert isinstance(llama_cpp, dict)
        llama_cpp["build"] = "b9999"

        self.assertNotEqual(
            self._load_raw(raw).runtime_fingerprint,
            self._load_raw(modified).runtime_fingerprint,
        )

    def test_compatibility_changes_change_recipe_fingerprint(self) -> None:
        raw = self._raw_manifest()
        modified = copy.deepcopy(raw)
        model = modified["model"]
        assert isinstance(model, dict)
        model["output_dimension"] = 1024

        self.assertNotEqual(
            self._load_raw(raw).recipe_fingerprint,
            self._load_raw(modified).recipe_fingerprint,
        )

    def test_effective_gemma_settings_change_runtime_and_recipe_compatibility(self) -> None:
        manifest = load_runtime_manifest()
        for settings in (
            {"batch_size": 1024},
            {"ubatch_size": 1024},
            {"flash_attention": "on"},
            {"vulkan_disable_f16": False},
        ):
            with self.subTest(settings=settings):
                changed = replace(
                    manifest,
                    llama_cpp=replace(manifest.llama_cpp, server=replace(manifest.llama_cpp.server, **settings)),
                )
                self.assertNotEqual(manifest.runtime_fingerprint, changed.runtime_fingerprint)
                self.assertNotEqual(manifest.recipe_fingerprint, changed.recipe_fingerprint)
        changed_prompt = replace(manifest, embedding=replace(manifest.embedding, instruction="task: search result | query:"))
        self.assertNotEqual(manifest.runtime_fingerprint, changed_prompt.runtime_fingerprint)
        self.assertNotEqual(manifest.recipe_fingerprint, changed_prompt.recipe_fingerprint)
        image_policy = replace(manifest, embedding=replace(manifest.embedding, image_input_policy="image-with-instruction"))
        self.assertNotEqual(manifest.runtime_fingerprint, image_policy.runtime_fingerprint)
        self.assertNotEqual(manifest.recipe_fingerprint, image_policy.recipe_fingerprint)

    def test_new_server_fields_are_validated_at_the_manifest_boundary(self) -> None:
        for field, value in (("batch_size", 0), ("ubatch_size", True), ("flash_attention", "on"), ("vulkan_disable_f16", "1")):
            raw = self._raw_manifest()
            raw["llama_cpp"]["server"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeManifestError, field):
                self._load_raw(raw)

    def test_paths_cannot_escape_project_root(self) -> None:
        raw = self._raw_manifest()
        paths = raw["paths"]
        assert isinstance(paths, dict)
        paths["log_dir"] = "../logs"

        with self.assertRaisesRegex(RuntimeManifestError, "safe project-relative"):
            self._load_raw(raw)


if __name__ == "__main__":
    unittest.main()
