"""Instance-owned Pinned Runtime lifecycle.

A ``PinnedRuntime`` owns runtime authorization state for one application
session and holds a lease on the single process-shared llama-server.  It is
created by the composition root (``LocalAppHost`` or ``create_app``) and closed
by the same owner.  One serialized inference scheduler and one llama-server
serve both indexing and search, so the shared server is torn down only when
the last live runtime releases its lease.
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import library
from .runtime_manifest import load_runtime_manifest
from .runtime_service import (
    RuntimeAuthorizationError,
    run_runtime_health_check,
    runtime_health_matches_manifest,
)


class PinnedRuntimeClosedError(RuntimeError):
    """The Pinned Runtime instance was already closed."""


class _SharedInferenceServer:
    """Explicit manager of the single process-shared llama-server.

    The manifest pins one llama-server and one serialized scheduler that serve
    both indexing and search, so the server process cannot be per-instance.
    Each live ``PinnedRuntime`` holds a lease on it; the server and the cached
    embedding backend are torn down only when the last lease is released, so
    closing one application host never stops a server that another live host
    still depends on.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases = 0

    def acquire(self) -> None:
        with self._lock:
            self._leases += 1

    def release(self) -> None:
        with self._lock:
            if self._leases == 0:
                return
            self._leases -= 1
            if self._leases > 0:
                return
        self._teardown()

    def _teardown(self) -> None:
        from . import embedding_backend
        from .llama_cpp_backend import close_managed_servers

        close_managed_servers()
        embedding_backend._get_cached_llama_cpp_backend.cache_clear()


_SHARED_INFERENCE_SERVER = _SharedInferenceServer()


class PinnedRuntime:
    """Own authorization, inference access, and shutdown for one app session."""

    def __init__(self, library_root: Path | str) -> None:
        self.library_root = Path(library_root).expanduser().resolve()
        self._lock = threading.Lock()
        self._session_health: library.RuntimeHealthResult | None = None
        self._closed = False
        _SHARED_INFERENCE_SERVER.acquire()

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

        _SHARED_INFERENCE_SERVER.release()
