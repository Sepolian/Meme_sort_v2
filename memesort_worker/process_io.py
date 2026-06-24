from __future__ import annotations

import atexit
import os
import sys
from typing import TextIO


_DEVNULL_STREAM: TextIO | None = None


def ensure_process_stdio() -> None:
    _ensure_stream("stdout")
    _ensure_stream("stderr")


def _ensure_stream(name: str) -> None:
    stream = getattr(sys, name, None)
    if stream is not None and hasattr(stream, "write"):
        return
    setattr(sys, name, _shared_devnull_stream())


def _shared_devnull_stream() -> TextIO:
    global _DEVNULL_STREAM
    if _DEVNULL_STREAM is None or _DEVNULL_STREAM.closed:
        _DEVNULL_STREAM = open(os.devnull, "w", encoding="utf-8")
    return _DEVNULL_STREAM


@atexit.register
def _close_devnull_stream() -> None:
    global _DEVNULL_STREAM
    if _DEVNULL_STREAM is not None and not _DEVNULL_STREAM.closed:
        _DEVNULL_STREAM.close()
    _DEVNULL_STREAM = None
