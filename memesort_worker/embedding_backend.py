from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np


class EmbeddingBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    """Runtime wiring retained at service boundaries while Vulkan owns execution."""

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
    runtime_manifest_path: str | None = None


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


def get_embedding_backend(
    backend_name: str,
    runtime_config: EmbeddingRuntimeConfig | None = None,
) -> EmbeddingBackend:
    if backend_name != "llama.cpp":
        raise EmbeddingBackendError(
            f"Unsupported embedding backend: {backend_name}. "
            "MemeSort is Vulkan-only and requires llama.cpp."
        )
    return _get_cached_llama_cpp_backend(runtime_config or EmbeddingRuntimeConfig())


class LlamaCppEmbeddingBackend(EmbeddingBackend):
    def __init__(self, runtime_config: EmbeddingRuntimeConfig) -> None:
        from .llama_cpp_backend import (
            LlamaCppBackendError,
            LlamaCppEmbeddingAdapter,
            load_server_config,
        )
        from .runtime_manifest import load_runtime_manifest

        self._adapter_error = LlamaCppBackendError
        manifest = load_runtime_manifest(runtime_config.runtime_manifest_path)
        self._adapter = LlamaCppEmbeddingAdapter(load_server_config(manifest.source_path))
        super().__init__(
            backend_id=f"llama.cpp-vulkan::{manifest.recipe_fingerprint}"
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


@lru_cache(maxsize=1)
def _get_cached_llama_cpp_backend(
    runtime_config: EmbeddingRuntimeConfig,
) -> LlamaCppEmbeddingBackend:
    return LlamaCppEmbeddingBackend(runtime_config)


def _coerce_normalized_dimension(
    vector: np.ndarray,
    output_dimension: int,
) -> np.ndarray:
    if output_dimension <= 0:
        raise EmbeddingBackendError(f"Invalid output dimension: {output_dimension}")
    vector = np.asarray(vector)
    if vector.ndim != 1:
        raise EmbeddingBackendError(f"Expected a 1D embedding, got shape {vector.shape}")
    if vector.shape[0] != output_dimension:
        raise EmbeddingBackendError(
            f"Model returned dim {vector.shape[0]}, expected exactly {output_dimension}"
        )
    if not np.issubdtype(vector.dtype, np.number):
        raise EmbeddingBackendError(
            f"Model returned non-numeric embedding dtype {vector.dtype}"
        )
    if not np.all(np.isfinite(vector)):
        raise EmbeddingBackendError("Model returned NaN or infinite embedding values")
    result = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(result.astype(np.float64, copy=False)))
    if not np.isfinite(norm) or norm == 0:
        raise EmbeddingBackendError("Model returned zero vector")
    normalized = np.asarray(result / norm, dtype=np.float32)
    if not np.all(np.isfinite(normalized)):
        raise EmbeddingBackendError("Embedding normalization produced non-finite values")
    return normalized
