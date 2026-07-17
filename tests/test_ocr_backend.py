from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memesort_worker.ocr_backend import PaddleOcrWorkerBackend, get_ocr_backend


class _FakeProcess:
    stdin = None
    stdout = None


class PaddleOcrWorkerBackendTests(unittest.TestCase):
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
