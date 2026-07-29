from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from .runtime_manifest import RuntimeManifest, load_runtime_manifest


class LlamaCppBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class LlamaCppServerConfig:
    manifest_path: Path
    executable_path: Path
    model_path: Path
    mmproj_path: Path
    request_model: str
    media_marker: str
    device: str
    gpu_layers: int
    context_size: int
    parallel_slots: int
    pooling: str
    normalization: str
    startup_timeout_seconds: float
    request_timeout_seconds: float
    device_probe_timeout_seconds: float
    idle_timeout_seconds: float
    shutdown_grace_seconds: float
    log_dir: Path
    log_file_count: int
    log_max_bytes: int

    @classmethod
    def from_manifest(cls, manifest: RuntimeManifest) -> "LlamaCppServerConfig":
        return cls(
            manifest_path=manifest.source_path,
            executable_path=manifest.llama_server_path,
            model_path=manifest.main_model_path,
            mmproj_path=manifest.projector_path,
            request_model=manifest.model.request_model,
            media_marker=manifest.embedding.media_marker,
            device=manifest.platform.device,
            gpu_layers=manifest.llama_cpp.server.gpu_layers,
            context_size=manifest.llama_cpp.server.context_size,
            parallel_slots=manifest.llama_cpp.server.parallel_slots,
            pooling=manifest.embedding.pooling,
            normalization=manifest.embedding.normalization,
            startup_timeout_seconds=manifest.llama_cpp.server.startup_timeout_seconds,
            request_timeout_seconds=manifest.llama_cpp.server.request_timeout_seconds,
            device_probe_timeout_seconds=(
                manifest.llama_cpp.server.device_probe_timeout_seconds
            ),
            idle_timeout_seconds=manifest.llama_cpp.server.idle_timeout_seconds,
            shutdown_grace_seconds=manifest.shutdown_grace_seconds,
            log_dir=manifest.log_dir,
            log_file_count=manifest.logging.file_count,
            log_max_bytes=manifest.logging.max_bytes_per_file,
        )


def load_server_config(
    manifest_path: str | Path | None = None,
) -> LlamaCppServerConfig:
    return LlamaCppServerConfig.from_manifest(load_runtime_manifest(manifest_path))


def discover_llama_server() -> Path:
    candidate = load_server_config().executable_path.resolve()
    if candidate.is_file():
        return candidate
    raise LlamaCppBackendError(
        f"Pinned llama-server executable does not exist: {candidate}. Run setup to "
        "activate the runtime declared by runtime-manifest.json."
    )


def verify_qwen3_vl_embedding_2b_bundle(
    main_model: Path,
    mmproj: Path,
    manifest: RuntimeManifest | None = None,
) -> None:
    manifest = manifest or load_runtime_manifest()
    expected = {
        main_model: manifest.model.main.sha256,
        mmproj: manifest.model.projector.sha256,
    }
    for path, expected_hash in expected.items():
        actual_hash = _sha256_file(path)
        if actual_hash != expected_hash:
            raise LlamaCppBackendError(
                f"Unexpected SHA256 for {path.name}: {actual_hash}. "
                "The active Vulkan recipe is pinned by runtime-manifest.json."
            )


class LlamaCppServer:
    """Own one local llama-server process, or connect to an explicitly supplied server."""

    def __init__(self, config: LlamaCppServerConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[bytes] | None = None
        self._base_url: str | None = None
        self._lock = threading.RLock()
        self._idle_timer: threading.Timer | None = None
        self._last_activity = 0.0
        self._logger = _runtime_logger(config)
        # Crash fallback only: the owning PinnedRuntime closes the server
        # explicitly; this guards against a llama-server child outliving an
        # interpreter that never reached that close.
        atexit.register(self.close)

    @property
    def base_url(self) -> str:
        with _ACTIVE_SERVER_LOCK:
            with self._lock:
                self._ensure_ready()
                assert self._base_url is not None
                return self._base_url

    def close(self) -> None:
        with self._lock:
            self._cancel_idle_timer()
            process = self._process
            self._process = None
            self._base_url = None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=self.config.shutdown_grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.config.shutdown_grace_seconds)
            if process is not None:
                self._logger.info("llama_server_stopped")

    def request_embedding(self, request_input: Any) -> np.ndarray:
        for attempt in range(2):
            try:
                with _ACTIVE_SERVER_LOCK:
                    with self._lock:
                        self._ensure_ready()
                        response = self._request_json(
                            "/v1/embeddings",
                            method="POST",
                            payload={
                                "input": request_input,
                                "model": self.config.request_model,
                                "encoding_format": "float",
                            },
                            timeout=self.config.request_timeout_seconds,
                        )
                        vector = _embedding_from_response(response)
                        self._touch_activity()
                        return vector
            except LlamaCppBackendError:
                if attempt == 1:
                    self._logger.error("embedding_request_failed_after_retry")
                    raise
                self._logger.warning("embedding_request_failed_restarting_once")
                self.close()
        raise AssertionError("unreachable")

    def _ensure_ready(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        _validate_manifest_runtime(self.config)
        main_model = self.config.model_path.resolve()
        mmproj = self.config.mmproj_path.resolve()
        if not main_model.is_file():
            raise LlamaCppBackendError(f"Pinned main GGUF does not exist: {main_model}")
        if not mmproj.is_file():
            raise LlamaCppBackendError(f"Pinned multimodal projector does not exist: {mmproj}")
        executable = self.config.executable_path.resolve()
        if not executable.is_file():
            raise LlamaCppBackendError(
                f"Pinned llama-server executable does not exist: {executable}. Run setup."
            )
        port = _find_available_local_port()
        self._base_url = f"http://127.0.0.1:{port}"
        command = [
            str(executable),
            "--model",
            str(main_model),
            "--mmproj",
            str(mmproj),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--embedding",
            "--pooling",
            self.config.pooling,
            "--embd-normalize",
            _llama_normalization_value(self.config.normalization),
            "--device",
            self.config.device,
            "--n-gpu-layers",
            str(self.config.gpu_layers),
            "--ctx-size",
            str(self.config.context_size),
            "--parallel",
            str(self.config.parallel_slots),
            "--log-disable",
        ]
        env = os.environ.copy()
        env["LLAMA_MEDIA_MARKER"] = self.config.media_marker
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise LlamaCppBackendError(f"Failed to start llama-server: {exc}") from exc
        self._logger.info("llama_server_started")
        try:
            self._wait_until_healthy()
            self._touch_activity()
        except Exception:
            self.close()
            raise

    def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise LlamaCppBackendError(
                    "llama-server exited during startup with code "
                    f"{self._process.returncode}."
                )
            try:
                response = self._request_json("/health", method="GET", timeout=2.0)
                if response.get("status") == "ok":
                    return
            except (LlamaCppBackendError, URLError, TimeoutError) as exc:
                last_error = exc
            time.sleep(0.2)
        raise LlamaCppBackendError(
            "llama-server did not become healthy within "
            f"{self.config.startup_timeout_seconds:g}s: {last_error or 'unknown error'}."
        )

    def _touch_activity(self) -> None:
        self._last_activity = time.monotonic()
        self._cancel_idle_timer()
        timer = threading.Timer(
            self.config.idle_timeout_seconds,
            self._close_if_idle,
        )
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _close_if_idle(self) -> None:
        with self._lock:
            idle_for = time.monotonic() - self._last_activity
            if idle_for >= self.config.idle_timeout_seconds:
                self._logger.info("llama_server_idle_unload")
                self.close()
            elif self._process is not None:
                self._touch_activity()

    def _cancel_idle_timer(self) -> None:
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()

    def _request_json(
        self,
        path: str,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self._base_url:
            raise LlamaCppBackendError("llama-server URL is not initialized")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self._base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LlamaCppBackendError(
                f"llama-server request failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LlamaCppBackendError(f"llama-server request failed: {exc}") from exc
        if not isinstance(decoded, dict):
            raise LlamaCppBackendError(f"llama-server returned non-object JSON: {decoded!r}")
        return decoded


class LlamaCppEmbeddingAdapter:
    def __init__(self, config: LlamaCppServerConfig) -> None:
        self.config = config
        self.server = LlamaCppServer(config)

    def close(self) -> None:
        self.server.close()

    def embed_text(self, text: str, instruction: str | None = None) -> np.ndarray:
        return self.server.request_embedding(_format_text_prompt(text, instruction))

    def embed_image_bytes(
        self,
        image_bytes: bytes,
        instruction: str | None = None,
    ) -> np.ndarray:
        prompt = _format_text_prompt(self.config.media_marker, instruction)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return self.server.request_embedding(
            [{"prompt_string": prompt, "multimodal_data": [encoded]}]
        )


def _format_text_prompt(content: str, instruction: str | None) -> str:
    if instruction:
        return f"{instruction.strip()}\n{content}"
    return content


def _embedding_from_response(response: dict[str, Any]) -> np.ndarray:
    try:
        embedding = response["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlamaCppBackendError(
            "llama-server returned an unexpected embeddings response"
        ) from exc
    vector = np.asarray(embedding, dtype=np.float32)
    if vector.ndim != 1 or vector.size == 0:
        raise LlamaCppBackendError(
            f"llama-server returned an invalid embedding shape: {vector.shape}"
        )
    return vector


def _runtime_logger(config: LlamaCppServerConfig) -> logging.Logger:
    log_path = (config.log_dir / "inference.log").resolve()
    key = str(log_path).casefold()
    with _LOGGER_LOCK:
        existing = _RUNTIME_LOGGERS.get(key)
        if existing is not None:
            return existing
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"memesort.inference.{len(_RUNTIME_LOGGERS)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            log_path,
            maxBytes=config.log_max_bytes,
            backupCount=max(0, config.log_file_count - 1),
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        _RUNTIME_LOGGERS[key] = logger
        return logger


def _close_runtime_loggers() -> None:
    with _LOGGER_LOCK:
        for logger in _RUNTIME_LOGGERS.values():
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        _RUNTIME_LOGGERS.clear()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def probe_llama_devices(
    executable_path: str | Path,
    timeout_seconds: float | None = None,
) -> str:
    timeout = (
        load_server_config().device_probe_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    try:
        completed = subprocess.run(
            [str(executable_path), "--list-devices"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LlamaCppBackendError(f"Failed to probe llama.cpp devices: {exc}") from exc
    output = b"\n".join([completed.stdout, completed.stderr]).decode(
        "utf-8", errors="replace"
    ).strip()
    if completed.returncode != 0:
        raise LlamaCppBackendError(
            f"llama-server --list-devices failed with code {completed.returncode}: {output}"
        )
    if not output:
        raise LlamaCppBackendError("llama-server --list-devices returned no devices")
    return output


def _llama_normalization_value(normalization: str) -> str:
    if normalization == "l2":
        return "2"
    raise LlamaCppBackendError(
        f"Unsupported llama.cpp embedding normalization: {normalization}"
    )


def _validate_manifest_runtime(config: LlamaCppServerConfig) -> None:
    from .runtime_activation import validate_runtime_activation
    from .runtime_admission import validate_pinned_runtime_files

    manifest = load_runtime_manifest(config.manifest_path)
    expected = LlamaCppServerConfig.from_manifest(manifest)
    if config != expected:
        raise LlamaCppBackendError(
            "llama.cpp configuration diverged from runtime-manifest.json."
        )
    validate_runtime_activation(manifest)
    validate_pinned_runtime_files(manifest)


def _find_available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_LOGGER_LOCK = threading.Lock()
_RUNTIME_LOGGERS: dict[str, logging.Logger] = {}
# Serializes llama-server requests across every server in the process so at
# most one inference runs at a time regardless of how many runtimes exist.
_ACTIVE_SERVER_LOCK = threading.RLock()

atexit.register(_close_runtime_loggers)
