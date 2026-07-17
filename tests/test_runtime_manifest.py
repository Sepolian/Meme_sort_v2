from __future__ import annotations

import copy
import json
import tempfile
import unittest
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
        self.assertEqual(2048, manifest.model.output_dimension)
        self.assertEqual("float32", manifest.embedding.storage_dtype)
        self.assertEqual(
            manifest.project_root / ".runtime" / "llama.cpp-b9982-vulkan" / "llama-server.exe",
            manifest.llama_server_path,
        )
        self.assertEqual(71, len(manifest.recipe_id))
        self.assertTrue(manifest.recipe_display_id.startswith("vulkan-"))

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

    def test_paths_cannot_escape_project_root(self) -> None:
        raw = self._raw_manifest()
        paths = raw["paths"]
        assert isinstance(paths, dict)
        paths["log_dir"] = "../logs"

        with self.assertRaisesRegex(RuntimeManifestError, "safe project-relative"):
            self._load_raw(raw)


if __name__ == "__main__":
    unittest.main()
