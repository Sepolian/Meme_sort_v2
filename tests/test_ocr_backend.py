from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from memesort_worker.ocr_backend import PaddleOcrWorkerBackend


class _FakeProcess:
    stdin = None
    stdout = None


class PaddleOcrWorkerBackendTests(unittest.TestCase):
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
