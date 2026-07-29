"""Instance-owned Pinned Runtime lifecycle.

A ``PinnedRuntime`` owns runtime authorization state for one application
session and the explicit shutdown of the managed llama-server process.  It is
created by the composition root (``LocalAppHost`` or ``create_app``) and closed
by the same owner.  One serialized inference scheduler and one llama-server
serve both indexing and search, so the scheduler and the embedding backend
remain process-shared behind this instance interface.
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import library
from .inference_service import INFERENCE_SCHEDULER, InferenceScheduler
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

    @property
    def scheduler(self) -> InferenceScheduler:
        return INFERENCE_SCHEDULER

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _require_open(self) -> None:
        with self._lock:
            if self._closed:
                raise PinnedRuntimeClosedError("The Pinned Runtime has been closed.")

    def run_health_check(self) -> library.RuntimeHealthResult:
        self._require_open()
        result = run_runtime_health_check(library_root=self.library_root)
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
        """Return the process-shared llama.cpp embedding backend.

        The backend is shared so indexing and search use the same
        llama-server and serialized scheduler (a manifest invariant).
        """
        self._require_open()
        from .embedding_backend import get_embedding_backend

        return get_embedding_backend()

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

        from . import embedding_backend
        from .llama_cpp_backend import close_managed_servers

        close_managed_servers()
        embedding_backend._get_cached_llama_cpp_backend.cache_clear()
