from __future__ import annotations

import sys

DEFAULT_MUTEX_NAME = "MemeSort-SingleInstance"
_ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """Ensure only one MemeSort instance owns the worker threads and runtime.

    Backed by a Windows named mutex rather than a lock file: the operating
    system reclaims the mutex when the process exits, so a crash never leaves a
    stale lock that blocks the next launch.
    """

    def __init__(self, name: str = DEFAULT_MUTEX_NAME) -> None:
        self._name = name
        self._handle = None
        self._is_primary = False

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def acquire(self) -> bool:
        """Return True when this process is the first (primary) instance."""
        if sys.platform != "win32":
            # Single-instance enforcement is a Windows product requirement; on
            # other platforms we do not block a second launch.
            self._is_primary = True
            return True

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]

        handle = kernel32.CreateMutexW(None, True, self._name)
        last_error = kernel32.GetLastError()
        if not handle:
            raise OSError("Failed to create the single-instance mutex")
        self._handle = handle
        self._is_primary = last_error != _ERROR_ALREADY_EXISTS
        return self._is_primary

    def release(self) -> None:
        if self._handle is None:
            return
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = None
        self._is_primary = False

    def __enter__(self) -> "SingleInstanceGuard":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
