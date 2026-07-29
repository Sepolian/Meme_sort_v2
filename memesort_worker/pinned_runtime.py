"""Instance-owned Pinned Runtime lifecycle.

A ``PinnedRuntime`` owns runtime authorization state, one serialized
inference scheduler, and the llama.cpp embedding backend (and therefore the
llama-server process) for one application session.  It is created by the
composition root (``LocalAppHost`` or ``create_app``) and closed by the same
owner; closing the runtime tears down exactly the resources it created.
Indexing and search both go through this instance, so one host still uses
one llama-server and one serialized scheduler (a manifest invariant).
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import library
from .inference_service import InferenceScheduler, search_inference_request
from .runtime_manifest import load_runtime_manifest
from .runtime_service import (
    RuntimeAuthorizationError,
    run_runtime_health_check,
    runtime_health_matches_manifest,
)


class PinnedRuntimeClosedError(RuntimeError):
    """The Pinned Runtime instance was already closed."""


class PinnedRuntime:
    """Own authorization, inference access, and shutdown for one app session."""

    def __init__(self, library_root: Path | str) -> None:
        self.library_root = Path(library_root).expanduser().resolve()
        self._lock = threading.Lock()
        self._session_health: library.RuntimeHealthResult | None = None
        self._closed = False
        self._scheduler = InferenceScheduler()
        self._embedding_backend = None

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def scheduler(self) -> InferenceScheduler:
        return self._scheduler

    def _require_open(self) -> None:
        with self._lock:
            if self._closed:
                raise PinnedRuntimeClosedError("The Pinned Runtime has been closed.")

    def run_health_check(self) -> library.RuntimeHealthResult:
        self._require_open()
        result = run_runtime_health_check(
            library_root=self.library_root,
            record_session_health=False,
            embedding_backend_factory=self.get_embedding_backend,
        )
        with self._lock:
            self._session_health = result
        return result

    def authorize(self) -> library.RuntimeHealthResult:
        result = self.run_health_check()
        if not result.smoke_test_ok:
            raise RuntimeAuthorizationError(
                result.error
                or "Vulkan runtime health check failed; work was not authorized."
            )
        return result

    def current_health_check(self) -> library.RuntimeHealthResult | None:
        with self._lock:
            return self._session_health

    def is_ready_for_indexing(self) -> tuple[bool, str]:
        with self._lock:
            if self._closed:
                return False, "The Pinned Runtime has been closed."
            current_health = self._session_health

        manifest = load_runtime_manifest()
        if not manifest.llama_server_path.is_file():
            return False, "Pinned llama-server is not installed. Run setup."
        if not manifest.main_model_path.is_file():
            return False, "Pinned main GGUF is not installed. Run setup."
        if not manifest.projector_path.is_file():
            return False, "Pinned multimodal projector is not installed. Run setup."

        if current_health is None:
            return False, "Vulkan runtime health has not been checked in this app session."
        if not runtime_health_matches_manifest(current_health):
            return False, "This session's runtime health check is stale for the active manifest."
        if not current_health.smoke_test_ok:
            return False, current_health.error or "Vulkan runtime health check failed."
        return True, "Runtime is ready for indexing."

    def get_embedding_backend(self):
        """Return this runtime's llama.cpp embedding backend, creating it lazily.

        The backend is bound to this runtime's scheduler so indexing and
        search on the same host share one llama-server and one serialized
        scheduler.
        """
        self._require_open()
        with self._lock:
            if self._embedding_backend is None:
                from .embedding_backend import LlamaCppEmbeddingBackend

                self._embedding_backend = LlamaCppEmbeddingBackend(self._scheduler)
            return self._embedding_backend

    def search_request(self, request_id: str):
        """Enter a prioritized search context on this runtime's scheduler."""
        self._require_open()
        return search_inference_request(self._scheduler, request_id)

    def cancel_search(self, request_id: str) -> bool:
        """Cancel a queued or running search request on this runtime."""
        self._require_open()
        return self._scheduler.cancel(request_id)

    def get_ocr_backend(self):
        """Return a CPU-only OCR backend; the caller owns closing it."""
        self._require_open()
        from .ocr_backend import get_ocr_backend

        return get_ocr_backend(self.library_root, "llama.cpp")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._session_health = None
            backend = self._embedding_backend
            self._embedding_backend = None

        if backend is not None:
            backend.close()
