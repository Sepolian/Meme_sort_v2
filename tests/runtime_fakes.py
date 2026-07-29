"""In-memory fake runtime adapter for indexing tests.

The fake satisfies the pipeline's IndexingRuntime protocol without an HTTP
server or llama process, returns deterministic embeddings and OCR results,
and can model authorization failure, embedding failure, and cancellation.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from memesort_worker.inference_service import (
    InferenceCancelledError,
    InferenceScheduler,
    search_inference_request,
)
from memesort_worker.runtime_manifest import load_runtime_manifest


class FakeEmbeddingBackend:
    backend_id = "fake-runtime::deterministic"

    def __init__(
        self,
        fail_with: Exception | None = None,
        cancel_requests: bool = False,
    ) -> None:
        manifest = load_runtime_manifest()
        vector = np.ones(manifest.model.output_dimension, dtype=np.float32)
        self.vector = vector / np.linalg.norm(vector)
        self._fail_with = fail_with
        self._cancel_requests = cancel_requests

    def _embed(self) -> np.ndarray:
        if self._cancel_requests:
            raise InferenceCancelledError("fake inference request was cancelled")
        if self._fail_with is not None:
            raise self._fail_with
        return self.vector

    def embed_text(self, text, output_dimension, instruction=None) -> np.ndarray:
        del text, instruction, output_dimension
        return self._embed()

    def embed_image_bytes(self, image_bytes, output_dimension, instruction=None) -> np.ndarray:
        del image_bytes, instruction, output_dimension
        return self._embed()


class FakeOcrBackend:
    backend_id = "fake-ocr"

    def __init__(self) -> None:
        self.closed = False

    def recognize_image(self, image_path: Path) -> dict[str, object]:
        return {
            "engine": self.backend_id,
            "text": image_path.stem,
            "texts": [image_path.stem],
            "scores": [1.0],
            "boxes": [[]],
            "language_hint": "test",
        }

    def close(self) -> None:
        self.closed = True


class FakeIndexingRuntime:
    """Second adapter for the pipeline's runtime seam."""

    def __init__(
        self,
        ready: bool = True,
        ready_message: str = "fake runtime is ready",
        embedding_backend: FakeEmbeddingBackend | None = None,
        ocr_backend: FakeOcrBackend | None = None,
    ) -> None:
        self._ready = ready
        self._ready_message = ready_message
        self.embedding_backend = embedding_backend or FakeEmbeddingBackend()
        self.ocr_backend = ocr_backend or FakeOcrBackend()
        self.scheduler = InferenceScheduler()

    def is_ready_for_indexing(self) -> tuple[bool, str]:
        return self._ready, self._ready_message

    def get_embedding_backend(self) -> FakeEmbeddingBackend:
        return self.embedding_backend

    def get_ocr_backend(self) -> FakeOcrBackend:
        return self.ocr_backend

    def search_request(self, request_id: str):
        return search_inference_request(self.scheduler, request_id)

    def cancel_search(self, request_id: str) -> bool:
        return self.scheduler.cancel(request_id)
