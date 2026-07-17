from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memesort_worker.ocr_backend import PaddleOcrWorkerBackend, get_ocr_backend


class _FakeProcess:
    stdin = None
    stdout = None


class PaddleOcrWorkerBackendTests(unittest.TestCase):
    def test_setup_installs_and_verifies_the_two_pinned_ocr_packages(self) -> None:
        root = Path(__file__).resolve().parents[1]
        setup_script = (root / "scripts" / "setup_windows_llama.ps1").read_text(
            encoding="utf-8"
        )
        requirements = (root / "requirements-ocr.txt").read_text(encoding="utf-8")

        self.assertIn('"paddlepaddle==3.2.2"', setup_script)
        self.assertIn('"https://www.paddlepaddle.org.cn/packages/stable/cpu/"', setup_script)
        self.assertIn("Failed to install pinned PaddlePaddle CPU runtime.", setup_script)
        self.assertIn("Failed to install pinned PaddleOCR worker dependencies.", setup_script)
        self.assertIn("Pinned PaddleOCR environment verification failed.", setup_script)
        self.assertIn("paddleocr==3.6.0", requirements)
        self.assertNotIn("paddlepaddle==", requirements)

    def test_missing_pinned_ocr_environment_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "memesort_worker.ocr_backend._ocr_python_path",
            return_value=Path(temp_dir) / "missing-python.exe",
        ):
            with self.assertRaisesRegex(RuntimeError, "Pinned OCR environment is missing"):
                get_ocr_backend(Path(temp_dir), "llama.cpp")

    @patch("memesort_worker.ocr_backend.subprocess.Popen")
    def test_worker_uses_cpu_and_project_local_model_cache(self, popen) -> None:
        popen.return_value = _FakeProcess()

        with patch.dict(
            os.environ,
            {
                "PADDLE_PDX_CACHE_HOME": r"C:\\Users\\example\\.paddlex",
                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "False",
            },
        ):
            PaddleOcrWorkerBackend(
                Path(".venv-ocr/Scripts/python.exe"),
                Path("scripts/paddle_ocr_worker.py"),
            )

        command = popen.call_args.args[0]
        worker_env = popen.call_args.kwargs["env"]
        self.assertEqual(worker_env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(worker_env["PYTHONUTF8"], "1")
        self.assertEqual(popen.call_args.kwargs["errors"], "strict")
        self.assertEqual(command[-1], "cpu")
        self.assertTrue(
            Path(worker_env["PADDLE_PDX_CACHE_HOME"]).as_posix().endswith(
                "/.models/paddleocr"
            )
        )
        self.assertEqual(worker_env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"], "True")


if __name__ == "__main__":
    unittest.main()
