from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from memesort_worker.runtime_activation import (
    RuntimeActivationError,
    expected_activation_record,
    validate_runtime_activation,
    write_runtime_activation,
)
from memesort_worker.runtime_manifest import load_runtime_manifest


class RuntimeActivationTests(unittest.TestCase):
    def _temporary_manifest(self, root: Path):
        return replace(
            load_runtime_manifest(),
            source_path=root / "runtime-manifest.json",
        )

    def test_missing_activation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = self._temporary_manifest(Path(temp_dir))

            with self.assertRaisesRegex(RuntimeActivationError, "not activated"):
                validate_runtime_activation(manifest)

    def test_write_is_exact_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = self._temporary_manifest(Path(temp_dir))

            path = write_runtime_activation(manifest)
            validate_runtime_activation(manifest)

            self.assertEqual(
                expected_activation_record(manifest),
                json.loads(path.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_manifest_runtime_change_invalidates_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = self._temporary_manifest(Path(temp_dir))
            write_runtime_activation(manifest)
            changed = replace(
                manifest,
                llama_cpp=replace(manifest.llama_cpp, build="different-build"),
            )

            with self.assertRaisesRegex(RuntimeActivationError, "does not match"):
                validate_runtime_activation(changed)


if __name__ == "__main__":
    unittest.main()
