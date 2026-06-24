from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .webapp import run_web_app


def default_library_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MemeSort"
    return Path.home() / "AppData" / "Roaming" / "MemeSort"


def resolve_preferred_port(
    host: str,
    preferred_port: int,
) -> int:
    if preferred_port < 0:
        raise ValueError("preferred_port must be zero or positive")
    if preferred_port == 0:
        return 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, preferred_port))
    except OSError:
        return 0
    finally:
        sock.close()
    return preferred_port


def launch_local_mvp_app(
    library_root: Path | str | None = None,
    host: str = "127.0.0.1",
    preferred_port: int = 8765,
    open_browser: bool = True,
) -> None:
    resolved_library_root = (
        Path(library_root).expanduser().resolve()
        if library_root is not None
        else default_library_root().resolve()
    )
    resolved_library_root.mkdir(parents=True, exist_ok=True)
    listen_port = resolve_preferred_port(host, preferred_port)

    on_started = None
    if open_browser:
        on_started = _browser_open_callback

    run_web_app(
        str(resolved_library_root),
        host=host,
        port=listen_port,
        on_started=on_started,
    )


def _browser_open_callback(payload: dict[str, object]) -> None:
    url = str(payload["url"])
    thread = threading.Thread(
        target=_open_browser_when_ready,
        args=(url,),
        name="MemeSortOpenBrowser",
        daemon=True,
    )
    thread.start()


def _open_browser_when_ready(url: str) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0):
                break
        except Exception:
            time.sleep(0.2)
    webbrowser.open(url)
