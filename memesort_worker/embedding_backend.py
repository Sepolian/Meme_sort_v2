from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .inference_service import INFERENCE_SCHEDULER


class EmbeddingBackendError(RuntimeError):
    pass


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


def get_embedding_backend() -> EmbeddingBackend:
    return _get_cached_llama_cpp_backend()


class LlamaCppEmbeddingBackend(EmbeddingBackend):
    def __init__(self) -> None:
        from .llama_cpp_backend import (
            LlamaCppBackendError,
            LlamaCppEmbeddingAdapter,
            load_server_config,
        )
        from .runtime_manifest import load_runtime_manifest

        self._adapter_error = LlamaCppBackendError
        manifest = load_runtime_manifest()
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
            vector = INFERENCE_SCHEDULER.submit(
                lambda: self._adapter.embed_text(text, instruction=instruction)
            )
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
            vector = INFERENCE_SCHEDULER.submit(
                lambda: self._adapter.embed_image_bytes(
                    image_bytes,
                    instruction=instruction,
                )
            )
        except self._adapter_error as exc:
            raise EmbeddingBackendError(str(exc)) from exc
        return _coerce_normalized_dimension(vector, output_dimension)


@lru_cache(maxsize=1)
def _get_cached_llama_cpp_backend() -> LlamaCppEmbeddingBackend:
    return LlamaCppEmbeddingBackend()


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
