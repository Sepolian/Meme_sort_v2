from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import socket
import subprocess
import tempfile
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
    shutdown_grace_seconds: float

    @classmethod
    def from_manifest(cls, manifest: RuntimeManifest) -> "LlamaCppServerConfig":
        return cls(
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
            shutdown_grace_seconds=manifest.shutdown_grace_seconds,
        )


def load_server_config(
    manifest_path: str | Path | None = None,
) -> LlamaCppServerConfig:
    return LlamaCppServerConfig.from_manifest(load_runtime_manifest(manifest_path))


def resolve_gguf_bundle(model_name_or_path: str) -> tuple[Path, Path]:
    """Resolve a main GGUF and its multimodal projector from a file or folder."""
    source = Path(model_name_or_path).expanduser().resolve()
    if not source.exists():
        raise LlamaCppBackendError(f"GGUF model source does not exist: {source}")

    if source.is_file():
        if source.suffix.lower() != ".gguf" or source.name.lower().startswith("mmproj"):
            raise LlamaCppBackendError(
                "GGUF model source must be the main .gguf file, not the mmproj file."
            )
        main_model = source
        bundle_dir = source.parent
    else:
        bundle_dir = source
        candidates = sorted(
            path
            for path in bundle_dir.glob("*.gguf")
            if not path.name.lower().startswith("mmproj")
        )
        if not candidates:
            raise LlamaCppBackendError(f"No main .gguf model found in: {bundle_dir}")
        if len(candidates) > 1:
            q4_candidates = [path for path in candidates if "q4_k_m" in path.name.lower()]
            if len(q4_candidates) == 1:
                candidates = q4_candidates
            else:
                names = ", ".join(path.name for path in candidates)
                raise LlamaCppBackendError(
                    "Multiple main GGUF files found. Configure the exact main file instead: "
                    f"{names}"
                )
        main_model = candidates[0]

    mmproj_candidates = sorted(bundle_dir.glob("mmproj*.gguf"))
    if not mmproj_candidates:
        raise LlamaCppBackendError(
            f"No mmproj*.gguf multimodal projector found beside: {main_model}"
        )
    if len(mmproj_candidates) > 1:
        f16_candidates = [path for path in mmproj_candidates if "f16" in path.name.lower()]
        if len(f16_candidates) == 1:
            mmproj_candidates = f16_candidates
        else:
            names = ", ".join(path.name for path in mmproj_candidates)
            raise LlamaCppBackendError(
                "Multiple mmproj GGUF files found. Keep one projector beside the model: "
                f"{names}"
            )
    return main_model, mmproj_candidates[0]


def discover_llama_server(executable_path: str | Path | None = None) -> Path:
    candidate = Path(executable_path or load_server_config().executable_path).resolve()
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
        self._log_file: Any | None = None
        self._base_url: str | None = None
        self._lock = threading.RLock()

    @property
    def base_url(self) -> str:
        with _ACTIVE_SERVER_LOCK:
            with self._lock:
                self._ensure_ready()
                assert self._base_url is not None
                return self._base_url

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=self.config.shutdown_grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.config.shutdown_grace_seconds)
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None

    def request_embedding(self, request_input: Any) -> np.ndarray:
        _ = self.base_url
        payload = {
            "input": request_input,
            "model": self.config.request_model,
            "encoding_format": "float",
        }
        response = self._request_json(
            "/v1/embeddings",
            method="POST",
            payload=payload,
            timeout=self.config.request_timeout_seconds,
        )
        try:
            embedding = response["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlamaCppBackendError(
                f"llama-server returned an unexpected embeddings response: {response!r}"
            ) from exc
        vector = np.asarray(embedding, dtype=np.float32)
        if vector.ndim != 1 or vector.size == 0:
            raise LlamaCppBackendError(
                f"llama-server returned an invalid embedding shape: {vector.shape}"
            )
        return vector

    def _ensure_ready(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        main_model = self.config.model_path.resolve()
        mmproj = self.config.mmproj_path.resolve()
        if not main_model.is_file():
            raise LlamaCppBackendError(f"Pinned main GGUF does not exist: {main_model}")
        if not mmproj.is_file():
            raise LlamaCppBackendError(f"Pinned multimodal projector does not exist: {mmproj}")
        executable = discover_llama_server(self.config.executable_path)
        _activate_managed_server(self)
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
        ]
        env = os.environ.copy()
        env["LLAMA_MEDIA_MARKER"] = self.config.media_marker
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._log_file = tempfile.TemporaryFile(mode="w+b")
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=creation_flags,
            )
        except OSError as exc:
            self._log_file.close()
            self._log_file = None
            raise LlamaCppBackendError(f"Failed to start llama-server: {exc}") from exc
        try:
            self._wait_until_healthy()
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
                    f"{self._process.returncode}. Log tail: {self._read_log_tail()}"
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
            f"{self.config.startup_timeout_seconds:g}s: {last_error or 'unknown error'}. "
            f"Log tail: {self._read_log_tail()}"
        )

    def _read_log_tail(self, max_bytes: int = 4096) -> str:
        if self._log_file is None:
            return "unavailable"
        try:
            self._log_file.flush()
            size = self._log_file.seek(0, 2)
            self._log_file.seek(max(0, size - max_bytes))
            return self._log_file.read().decode("utf-8", errors="replace").strip() or "empty"
        except OSError:
            return "unavailable"

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
        _MANAGED_SERVERS.add(self.server)

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


def _find_available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_MANAGED_SERVERS: set[LlamaCppServer] = set()
_ACTIVE_SERVER_LOCK = threading.RLock()


def _activate_managed_server(server: LlamaCppServer) -> None:
    with _ACTIVE_SERVER_LOCK:
        for other in list(_MANAGED_SERVERS):
            if other is not server and other._process is not None:
                other.close()


@atexit.register
def _close_managed_servers() -> None:
    for server in list(_MANAGED_SERVERS):
        server.close()
