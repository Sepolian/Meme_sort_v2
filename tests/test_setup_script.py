from __future__ import annotations

import unittest
from pathlib import Path


class SetupScriptTests(unittest.TestCase):
    def test_tauri_portable_is_the_only_desktop_distribution_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

        self.assertNotIn("pywebview", pyproject)
        self.assertNotIn("memesort-desktop", pyproject)
        for retired_path in (
            "memesort_desktop.spec",
            "scripts/build_windows_bundle.ps1",
            "start_memesort.ps1",
            "start_memesort.bat",
            "memesort_worker/desktop_app.py",
            "memesort_worker/desktop_entry.py",
        ):
            with self.subTest(retired_path=retired_path):
                self.assertFalse((root / retired_path).exists())

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

    def test_portable_setup_installs_only_under_portable_data_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "setup_portable_runtime.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[string]$PortableRoot = $PSScriptRoot", script)
        self.assertIn("MemeSortData", script)
        self.assertIn("Convert-ManifestDataPath", script)
        self.assertIn("Portable manifest path must start with .runtime or .models", script)
        self.assertIn("runtime\\ocr-venv", script)
        self.assertIn("--write-runtime-activation", script)
        self.assertIn("$manifest.platform.device -ne \"Vulkan0\"", script)
        self.assertIn("Assert-VerifiedFile", script)
        self.assertIn("PADDLE_PDX_CACHE_HOME", script)
        self.assertIn("Failed to provision the pinned PaddleOCR models.", script)
        self.assertIn("$ocrWorker --lang ch --device cpu", script)
        self.assertNotIn("UV_PROJECT_ENVIRONMENT", script)

    def test_portable_build_includes_the_provisioning_resources_but_not_artifacts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "build_portable.ps1").read_text(encoding="utf-8")

        self.assertIn("setup_portable_runtime.ps1", script)
        self.assertIn("requirements-ocr.txt", script)
        self.assertIn("paddle_ocr_worker.py", script)
        self.assertNotIn("Copy-Item -Recurse -LiteralPath (Join-Path $repoRoot \".models\")", script)


if __name__ == "__main__":
    unittest.main()
