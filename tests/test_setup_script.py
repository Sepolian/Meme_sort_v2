from __future__ import annotations

import unittest
from pathlib import Path


class SetupScriptTests(unittest.TestCase):
    def test_launcher_validates_the_manifest_owned_runtime(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "start_memesort.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("runtime_activation validate --manifest", script)
        self.assertIn("runtime-manifest.json", script)
        self.assertNotIn("MEMESORT_LLAMA_SERVER", script)
        self.assertNotIn("llama.cpp-b9982-vulkan", script)

    def test_windows_setup_is_manifest_driven_and_activates_last(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "setup_windows_llama.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("ConvertFrom-Json", script)
        self.assertIn("runtime_activation write", script)
        self.assertIn("runtime_activation validate", script)
        self.assertIn("$manifest.platform.device -ne \"Vulkan0\"", script)
        self.assertIn("[switch]$Offline", script)
        self.assertIn("--continue-at", script)
        self.assertIn("Replace-DirectoryAtomically", script)
        self.assertNotIn("releases/latest", script)
        self.assertNotIn("42a4ebc629ecc651", script)
        self.assertNotIn("3f89a7768ffa6606", script)


if __name__ == "__main__":
    unittest.main()
