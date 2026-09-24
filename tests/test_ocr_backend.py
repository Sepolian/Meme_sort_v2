from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memesort_worker.ocr_backend import (
    PaddleOcrWorkerBackend,
    _ocr_python_path,
    _ocr_worker_path,
    get_ocr_backend,
)


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
                get_ocr_backend()

    def test_portable_ocr_paths_are_under_meme_sort_data_and_portable_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portable_root = Path(temp_dir) / "MemeSort-portable"
            with patch.dict(
                os.environ,
                {"MEMESORT_PORTABLE_ROOT": str(portable_root)},
                clear=True,
            ):
                self.assertEqual(
                    _ocr_python_path(),
                    portable_root.resolve()
                    / "MemeSortData"
                    / "runtime"
                    / "ocr-venv"
                    / "Scripts"
                    / "python.exe",
                )
                self.assertEqual(
                    _ocr_worker_path(),
                    portable_root.resolve() / "scripts" / "paddle_ocr_worker.py",
                )

    @patch("memesort_worker.ocr_backend.subprocess.Popen")
    def test_worker_uses_cpu_and_project_local_model_cache(self, popen) -> None:
        popen.return_value = _FakeProcess()

        with tempfile.TemporaryDirectory() as temp_dir:
            portable_root = Path(temp_dir) / "MemeSort-portable"
            with patch.dict(
                os.environ,
                {
                    "MEMESORT_PORTABLE_ROOT": str(portable_root),
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
                "/MemeSortData/models/paddleocr"
            )
        )
        self.assertEqual(worker_env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"], "True")


if __name__ == "__main__":
    unittest.main()
