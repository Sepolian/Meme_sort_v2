from __future__ import annotations

import logging
import threading
import uuid
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .launcher import default_library_root
from .local_app_host import LocalAppHost, LocalAppHostConfig
from .single_instance import SingleInstanceGuard

WINDOW_TITLE = "MemeSort"
DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 820
MIN_WINDOW_WIDTH = 960
MIN_WINDOW_HEIGHT = 640
SHUTDOWN_TIMEOUT_SECONDS = 15.0

_LOG = logging.getLogger("memesort.desktop")


def launch_desktop_shell(
    library_root: str | None = None,
    import_source: str | None = None,
    *,
    use_browser: bool = False,
    **_legacy_kwargs,
) -> int:
    """Run the MemeSort desktop application.

    A single :class:`LocalAppHost` owns the runtime; the window layer only opens
    a native WebView2 window (or a controlled browser fallback for development)
    at the host's authenticated bootstrap URL and performs a bounded, idempotent
    shutdown when the window closes.
    """
    session_id = uuid.uuid4().hex
    resolved_library_root = (
        Path(library_root).expanduser().resolve()
        if library_root
        else default_library_root().resolve()
    )
    logs_root = resolved_library_root / "logs"
    _configure_logging(logs_root, session_id)

    guard = SingleInstanceGuard()
    if not guard.acquire():
        _show_already_running()
        _LOG.info("second_instance_refused session=%s", session_id)
        return 0

    _LOG.info(
        "desktop_start session=%s library_root=%s browser=%s",
        session_id,
        resolved_library_root,
        use_browser,
    )

    host = LocalAppHost(LocalAppHostConfig(library_root=resolved_library_root))
    try:
        info = host.start()
    except Exception as exc:  # pragma: no cover - defensive startup guard
        _LOG.exception("host_start_failed session=%s", session_id)
        _show_startup_error(str(exc), logs_root)
        guard.release()
        return 1

    _LOG.info("host_running session=%s origin=%s", session_id, info.origin)
    try:
        if use_browser or not _webview_available():
            _run_browser_fallback(info.bootstrap_url)
        else:
            _run_webview_window(info.bootstrap_url)
    finally:
        report = host.stop(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        _LOG.info(
            "host_stopped session=%s clean=%s steps=%s",
            session_id,
            report.clean,
            [(step.name, round(step.duration_seconds, 3), step.timed_out) for step in report.steps],
        )
        guard.release()
    return 0


def run_smoke_test(library_root: str | None = None) -> int:
    """Start the host, confirm it serves, and shut down without any GUI.

    Packaged builds expose this so CI can verify frozen paths and the full
    lifecycle on a real Windows machine without needing to render a window.
    """
    import urllib.error
    import urllib.request

    resolved_library_root = (
        Path(library_root).expanduser().resolve()
        if library_root
        else default_library_root().resolve()
    )
    host = LocalAppHost(
        LocalAppHostConfig(
            library_root=resolved_library_root,
            authorize_runtime=False,
        )
    )
    info = host.start()
    try:
        request = urllib.request.Request(f"{info.origin}/api/state")
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            # 401 without a session proves the authenticated server is live.
            if exc.code != 401:
                raise
    finally:
        report = host.stop(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    return 0 if report.clean else 2


def _run_webview_window(bootstrap_url: str) -> None:
    import webview

    webview.create_window(
        WINDOW_TITLE,
        bootstrap_url,
        width=DEFAULT_WINDOW_WIDTH,
        height=DEFAULT_WINDOW_HEIGHT,
        min_size=(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
    )
    # Blocks until every window is closed; shutdown runs in the caller's finally.
    webview.start()


def _run_browser_fallback(bootstrap_url: str) -> None:
    _LOG.info("browser_fallback url=%s", bootstrap_url)
    webbrowser.open(bootstrap_url)
    stop_event = threading.Event()
    try:
        stop_event.wait()
    except KeyboardInterrupt:  # pragma: no cover - interactive dev path
        pass


def _webview_available() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def _configure_logging(logs_root: Path, session_id: str) -> None:
    if getattr(_LOG, "_memesort_configured", False):
        return
    logs_root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_root / "desktop.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False
    _LOG._memesort_configured = True  # type: ignore[attr-defined]


def _show_already_running() -> None:
    _show_message("MemeSort is already running.", "MemeSort")


def _show_startup_error(detail: str, logs_root: Path) -> None:
    message = (
        "MemeSort could not start.\n\n"
        f"{detail}\n\n"
        f"Logs are in:\n{logs_root}"
    )
    _show_message(message, "MemeSort - Startup Error")


def _show_message(message: str, title: str) -> None:
    import sys

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
            return
        except Exception:  # pragma: no cover - fall back to stderr
            pass
    print(f"{title}: {message}")
