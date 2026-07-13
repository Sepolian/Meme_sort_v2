from __future__ import annotations

import hashlib
import importlib
import io
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image


class EmbeddingBackendError(RuntimeError):
    pass


_CONFIGURED_TORCH_INTEROP_THREADS: int | None = None
_QWEN_TOKENIZER_RUNTIME_MODULES = ("sentencepiece", "tiktoken")


def _validate_qwen_tokenizer_runtime_dependencies() -> None:
    for module_name in _QWEN_TOKENIZER_RUNTIME_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError:
            continue
        return

    required_modules = " or ".join(
        f"`{module_name}`" for module_name in _QWEN_TOKENIZER_RUNTIME_MODULES
    )
    raise EmbeddingBackendError(
        "qwen3-vl tokenizer support requires "
        f"{required_modules} to be installed in .venv. "
        "Install the optional Qwen runtime with "
        "`.\\.venv\\Scripts\\python.exe -m pip install -r requirements-qwen.txt`."
    )


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    model_name_or_path: str | None = None
    torch_dtype: str = "auto"
    device: str | None = None
    low_cpu_mem_usage: bool = True
    num_threads: int | None = None
    num_interop_threads: int | None = None
    llama_server_path: str | None = None
    llama_server_url: str | None = None
    llama_gpu_layers: int = 99
    llama_context_size: int = 4096


@dataclass
class EmbeddingBackend:
    backend_id: str

    def embed_text(
        self,
        text: str,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        raise NotImplementedError

    def embed_image_bytes(
        self,
        image_bytes: bytes,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        raise NotImplementedError


class DebugHashEmbeddingBackend(EmbeddingBackend):
    """
    Development-only backend for validating the worker pipeline end-to-end.
    """

    def __init__(self) -> None:
        super().__init__(backend_id="debug-hash-v1")

    def embed_text(
        self,
        text: str,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        payload = f"text::{instruction or ''}::{text}".encode("utf-8")
        return _hash_to_unit_vector(payload, output_dimension)

    def embed_image_bytes(
        self,
        image_bytes: bytes,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        prefix = f"image::{instruction or ''}::".encode("utf-8")
        return _hash_to_unit_vector(prefix + image_bytes, output_dimension)


class Qwen3VLEmbeddingBackend(EmbeddingBackend):
    def __init__(self, runtime_config: EmbeddingRuntimeConfig) -> None:
        if not runtime_config.model_name_or_path:
            raise EmbeddingBackendError("qwen3-vl backend requires model_name_or_path")

        try:
            import torch
        except ImportError as exc:
            raise EmbeddingBackendError(
                "qwen3-vl backend requires `torch` to be installed in .venv"
            ) from exc

        try:
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise EmbeddingBackendError(
                "qwen3-vl backend requires `transformers` to be installed in .venv"
            ) from exc

        _validate_qwen_tokenizer_runtime_dependencies()

        try:
            from qwen_vl_utils import process_vision_info
        except ImportError as exc:
            raise EmbeddingBackendError(
                "qwen3-vl backend requires `qwen-vl-utils` to be installed in .venv"
            ) from exc

        self._torch = torch
        self._process_vision_info = process_vision_info
        self._configure_torch_threads(runtime_config)
        self._processor = AutoProcessor.from_pretrained(
            runtime_config.model_name_or_path,
            trust_remote_code=True,
            local_files_only=_is_local_model_path(runtime_config.model_name_or_path),
        )

        model_kwargs: dict[str, Any] = {
            "trust_remote_code": True,
            "low_cpu_mem_usage": runtime_config.low_cpu_mem_usage,
        }
        if runtime_config.torch_dtype != "auto":
            try:
                model_kwargs["dtype"] = getattr(torch, runtime_config.torch_dtype)
            except AttributeError as exc:
                raise EmbeddingBackendError(
                    f"Unsupported torch dtype: {runtime_config.torch_dtype}"
                ) from exc

        model = AutoModel.from_pretrained(
            runtime_config.model_name_or_path,
            local_files_only=_is_local_model_path(runtime_config.model_name_or_path),
            **model_kwargs,
        )
        if runtime_config.device:
            model = model.to(runtime_config.device)
        model.eval()

        self._model = model
        super().__init__(
            backend_id=(
                "qwen3-vl::"
                f"{runtime_config.model_name_or_path}::"
                f"{runtime_config.torch_dtype}::"
                f"threads={runtime_config.num_threads or 'default'}::"
                f"interop={runtime_config.num_interop_threads or 'default'}"
            )
        )

    def _configure_torch_threads(self, runtime_config: EmbeddingRuntimeConfig) -> None:
        if runtime_config.num_threads is not None:
            if runtime_config.num_threads <= 0:
                raise EmbeddingBackendError("num_threads must be positive")
            get_num_threads = getattr(self._torch, "get_num_threads", None)
            current_threads = get_num_threads() if callable(get_num_threads) else None
            if current_threads != runtime_config.num_threads:
                self._torch.set_num_threads(runtime_config.num_threads)
        if runtime_config.num_interop_threads is not None:
            if runtime_config.num_interop_threads <= 0:
                raise EmbeddingBackendError("num_interop_threads must be positive")
            self._configure_torch_interop_threads(runtime_config.num_interop_threads)

    def _configure_torch_interop_threads(self, requested_threads: int) -> None:
        global _CONFIGURED_TORCH_INTEROP_THREADS

        if _CONFIGURED_TORCH_INTEROP_THREADS == requested_threads:
            return

        get_num_interop_threads = getattr(self._torch, "get_num_interop_threads", None)
        current_threads = (
            get_num_interop_threads()
            if callable(get_num_interop_threads)
            else None
        )
        if current_threads == requested_threads:
            _CONFIGURED_TORCH_INTEROP_THREADS = requested_threads
            return

        try:
            self._torch.set_num_interop_threads(requested_threads)
        except RuntimeError as exc:
            current_threads = (
                get_num_interop_threads()
                if callable(get_num_interop_threads)
                else None
            )
            if current_threads == requested_threads:
                _CONFIGURED_TORCH_INTEROP_THREADS = requested_threads
                return
            raise EmbeddingBackendError(
                "Torch interop threads are already configured for this process. "
                f"Current={current_threads}, requested={requested_threads}."
            ) from exc

        _CONFIGURED_TORCH_INTEROP_THREADS = requested_threads

    def embed_text(
        self,
        text: str,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        messages = self._build_messages(
            content=[{"type": "text", "text": text}],
            instruction=instruction,
        )
        return self._run_embedding(messages=messages, output_dimension=output_dimension)

    def embed_image_bytes(
        self,
        image_bytes: bytes,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        messages = self._build_messages(
            content=[{"type": "image", "image": image}],
            instruction=instruction,
        )
        return self._run_embedding(messages=messages, output_dimension=output_dimension)

    def _build_messages(
        self,
        content: list[dict[str, Any]],
        instruction: str | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if instruction:
            messages.append(
                {
                    "role": "system",
                    "content": [{"type": "text", "text": instruction}],
                }
            )
        messages.append({"role": "user", "content": content})
        return messages

    def _run_embedding(
        self,
        messages: list[dict[str, Any]],
        output_dimension: int,
    ) -> np.ndarray:
        if output_dimension <= 0:
            raise EmbeddingBackendError(f"Invalid output dimension: {output_dimension}")

        text = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        image_inputs, video_inputs = self._process_vision_info(messages)
        inputs = self._processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = self._move_inputs(inputs)
        attention_mask = inputs.get("attention_mask")
        if attention_mask is None:
            raise EmbeddingBackendError("qwen3-vl inputs did not include attention_mask")

        with self._torch.inference_mode():
            outputs = self._model(**inputs)

        last_hidden_state = getattr(outputs, "last_hidden_state", None)
        if last_hidden_state is None and isinstance(outputs, dict):
            last_hidden_state = outputs.get("last_hidden_state")
        if last_hidden_state is None:
            raise EmbeddingBackendError(
                "qwen3-vl backend did not return `last_hidden_state` in model outputs"
            )

        pooled = self._pool_last_token(last_hidden_state, attention_mask)
        vector = pooled.detach().float().cpu().numpy()
        if vector.ndim != 2 or vector.shape[0] != 1:
            raise EmbeddingBackendError(
                f"Expected single-row embedding output, got shape {vector.shape}"
            )
        return self._coerce_output_dimension(np.asarray(vector[0], dtype=np.float32), output_dimension)

    def _move_inputs(self, inputs: Any) -> Any:
        if hasattr(inputs, "to"):
            return inputs.to(self._model.device)
        if isinstance(inputs, dict):
            moved: dict[str, Any] = {}
            for key, value in inputs.items():
                if hasattr(value, "to"):
                    moved[key] = value.to(self._model.device)
                else:
                    moved[key] = value
            return moved
        return inputs

    def _coerce_output_dimension(
        self,
        vector: np.ndarray,
        output_dimension: int,
    ) -> np.ndarray:
        current_dim = int(vector.shape[0])
        if current_dim == output_dimension:
            return self._normalize_vector(vector)
        if current_dim < output_dimension:
            raise EmbeddingBackendError(
                f"Model returned dim {current_dim}, smaller than requested {output_dimension}"
            )
        return self._normalize_vector(vector[:output_dimension])

    def _normalize_vector(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        if norm == 0:
            raise EmbeddingBackendError("Model returned zero vector")
        return vector / norm

    def _pool_last_token(self, hidden_state: Any, attention_mask: Any) -> Any:
        flipped = attention_mask.flip(dims=[1])
        last_one_positions = flipped.argmax(dim=1)
        col = attention_mask.shape[1] - last_one_positions - 1
        row = self._torch.arange(hidden_state.shape[0], device=hidden_state.device)
        return hidden_state[row, col]


@lru_cache(maxsize=8)
def _get_cached_qwen_backend(runtime_config: EmbeddingRuntimeConfig) -> Qwen3VLEmbeddingBackend:
    return Qwen3VLEmbeddingBackend(runtime_config)


def get_embedding_backend(
    backend_name: str,
    runtime_config: EmbeddingRuntimeConfig | None = None,
) -> EmbeddingBackend:
    if backend_name == "debug":
        return DebugHashEmbeddingBackend()
    if backend_name == "qwen3-vl":
        return _get_cached_qwen_backend(runtime_config or EmbeddingRuntimeConfig())
    if backend_name == "llama.cpp":
        return _get_cached_llama_cpp_backend(runtime_config or EmbeddingRuntimeConfig())
    raise EmbeddingBackendError(
        "Unsupported embedding backend: "
        f"{backend_name}. Supported backends: debug, qwen3-vl, llama.cpp"
    )


class LlamaCppEmbeddingBackend(EmbeddingBackend):
    def __init__(self, runtime_config: EmbeddingRuntimeConfig) -> None:
        if not runtime_config.model_name_or_path:
            raise EmbeddingBackendError("llama.cpp backend requires a local GGUF model source")
        from .llama_cpp_backend import (
            LlamaCppBackendError,
            LlamaCppEmbeddingAdapter,
            LlamaCppServerConfig,
        )

        self._adapter_error = LlamaCppBackendError
        self._adapter = LlamaCppEmbeddingAdapter(
            LlamaCppServerConfig(
                model_path=runtime_config.model_name_or_path,
                executable_path=runtime_config.llama_server_path,
                server_url=runtime_config.llama_server_url,
                gpu_layers=runtime_config.llama_gpu_layers,
                context_size=runtime_config.llama_context_size,
            )
        )
        super().__init__(
            backend_id=(
                f"llama.cpp::{runtime_config.model_name_or_path}::"
                f"gpu-layers={runtime_config.llama_gpu_layers}"
            )
        )

    def embed_text(
        self,
        text: str,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        try:
            vector = self._adapter.embed_text(text, instruction=instruction)
        except self._adapter_error as exc:
            raise EmbeddingBackendError(str(exc)) from exc
        return _coerce_normalized_dimension(vector, output_dimension)

    def embed_image_bytes(
        self,
        image_bytes: bytes,
        output_dimension: int,
        instruction: str | None = None,
    ) -> np.ndarray:
        try:
            vector = self._adapter.embed_image_bytes(image_bytes, instruction=instruction)
        except self._adapter_error as exc:
            raise EmbeddingBackendError(str(exc)) from exc
        return _coerce_normalized_dimension(vector, output_dimension)


@lru_cache(maxsize=8)
def _get_cached_llama_cpp_backend(
    runtime_config: EmbeddingRuntimeConfig,
) -> LlamaCppEmbeddingBackend:
    return LlamaCppEmbeddingBackend(runtime_config)


def _coerce_normalized_dimension(vector: np.ndarray, output_dimension: int) -> np.ndarray:
    if output_dimension <= 0:
        raise EmbeddingBackendError(f"Invalid output dimension: {output_dimension}")
    vector = np.asarray(vector, dtype=np.float32)
    if vector.ndim != 1:
        raise EmbeddingBackendError(f"Expected a 1D embedding, got shape {vector.shape}")
    if vector.shape[0] < output_dimension:
        raise EmbeddingBackendError(
            f"Model returned dim {vector.shape[0]}, smaller than requested {output_dimension}"
        )
    result = vector[:output_dimension]
    norm = np.linalg.norm(result)
    if norm == 0:
        raise EmbeddingBackendError("Model returned zero vector")
    return result / norm


def _hash_to_unit_vector(payload: bytes, output_dimension: int) -> np.ndarray:
    if output_dimension <= 0:
        raise EmbeddingBackendError(f"Invalid output dimension: {output_dimension}")

    buffer = bytearray()
    counter = 0
    while len(buffer) < output_dimension * 4:
        digest = hashlib.sha256(payload + counter.to_bytes(4, "little")).digest()
        buffer.extend(digest)
        counter += 1

    raw = np.frombuffer(bytes(buffer[: output_dimension * 4]), dtype=np.uint32)
    vector = raw.astype(np.float32)
    vector = (vector / np.float32(2**32 - 1)) * 2.0 - 1.0
    norm = np.linalg.norm(vector)
    if norm == 0:
        raise EmbeddingBackendError("Failed to build non-zero vector")
    return vector / norm


def _is_local_model_path(model_name_or_path: str | None) -> bool:
    if not model_name_or_path:
        return False
    if os.path.isabs(model_name_or_path):
        return True
    if model_name_or_path.startswith((".", ".\\", "./")):
        return True
    return os.path.exists(model_name_or_path)
