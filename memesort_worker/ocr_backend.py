from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Protocol


class OcrBackend(Protocol):
    backend_id: str

    def recognize_image(self, image_path: Path) -> dict[str, object]:
        ...

    def close(self) -> None:
        ...


class DebugOcrBackend:
    backend_id = "debug-ocr"

    def recognize_image(self, image_path: Path) -> dict[str, object]:
        text = image_path.stem.replace("_", " ").replace("-", " ")
        return {
            "engine": self.backend_id,
            "texts": [text] if text else [],
            "scores": [1.0] if text else [],
            "boxes": [[]] if text else [],
            "text": text,
            "language_hint": "debug",
        }

    def close(self) -> None:
        return


class PaddleOcrWorkerBackend:
    backend_id = "paddleocr-worker"

    def __init__(
        self,
        python_executable: Path,
        worker_script: Path,
        *,
        lang: str = "ch",
        device: str = "cpu",
    ) -> None:
        worker_env = os.environ.copy()
        worker_env.setdefault(
            "PADDLE_PDX_CACHE_HOME",
            str(_project_root() / ".models" / "paddleocr"),
        )
        worker_env.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        # The parent and worker exchange one JSON object per line.  On Windows a
        # redirected Python stdout can otherwise inherit the active ANSI code
        # page while this process decodes the pipe as UTF-8, silently damaging
        # CJK OCR text before it reaches SQLite.
        worker_env["PYTHONIOENCODING"] = "utf-8"
        worker_env["PYTHONUTF8"] = "1"
        self._process = subprocess.Popen(
            [
                str(python_executable),
                str(worker_script),
                "--lang",
                lang,
                "--device",
                device,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=worker_env,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if sys.platform.startswith("win")
                else 0
            ),
        )

    def recognize_image(self, image_path: Path) -> dict[str, object]:
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("PaddleOCR worker stdio is not available")
        request_id = str(uuid.uuid4())
        self._process.stdin.write(
            json.dumps(
                {
                    "id": request_id,
                    "path": str(image_path.resolve()),
                },
                ensure_ascii=False,
            )
        )
        self._process.stdin.write("\n")
        self._process.stdin.flush()

        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("PaddleOCR worker stopped unexpectedly.")
        payload = json.loads(line)
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        if payload.get("id") != request_id:
            raise RuntimeError("PaddleOCR worker returned an out-of-order response")
        result = dict(payload.get("result") or {})
        result.setdefault("engine", self.backend_id)
        return result

    def close(self) -> None:
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=2)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ocr_python_path() -> Path:
    configured = os.environ.get("MEMESORT_OCR_PYTHON")
    if configured:
        return Path(configured).expanduser().resolve()
    root = _project_root()
    return root / ".venv-ocr" / "Scripts" / "python.exe"


def get_ocr_backend(library_root: Path, embedding_backend_name: str) -> OcrBackend:
    requested_backend = os.environ.get("MEMESORT_OCR_BACKEND", "").strip().lower()
    python_executable = _ocr_python_path()
    worker_script = _project_root() / "scripts" / "paddle_ocr_worker.py"
    should_use_paddle = requested_backend in {"paddleocr", "paddleocr-worker"} or (
        requested_backend == ""
        and embedding_backend_name != "debug"
        and python_executable.exists()
    )
    if should_use_paddle and python_executable.exists():
        return PaddleOcrWorkerBackend(
            python_executable,
            worker_script,
            lang=os.environ.get("MEMESORT_OCR_LANG", "ch"),
            device=os.environ.get("MEMESORT_OCR_DEVICE", "cpu"),
        )

    if embedding_backend_name == "debug" or requested_backend in {"", "debug"}:
        return DebugOcrBackend()

    return DebugOcrBackend()
