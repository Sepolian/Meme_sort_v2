from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from wsgiref.simple_server import make_server

from .app_paths import AppPaths
from .pinned_runtime import PinnedRuntime
from .runtime_service import RuntimeAuthorizationError
from .web_security import SessionGate
from .webapp import (
    LocalWebApp,
    QuietWSGIRequestHandler,
    ThreadedWSGIServer,
    create_app,
)


class LocalAppHostError(RuntimeError):
    """The host could not be started or transitioned."""


@dataclass(frozen=True)
class LocalAppHostConfig:
    library_root: Path
    host: str = "127.0.0.1"
    port: int = 0
    authorize_runtime: bool = True
    static_root: Path | None = None


@dataclass(frozen=True)
class LocalAppHostInfo:
    origin: str
    bootstrap_url: str
    library_root: Path


@dataclass(frozen=True)
class ShutdownStep:
    name: str
    duration_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class ShutdownReport:
    steps: tuple[ShutdownStep, ...] = ()
    clean: bool = True
    authorization_error: str | None = None


# Lifecycle states.
STATE_NEW = "new"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"
STATE_STOPPED = "stopped"


class LocalAppHost:
    """Own the complete lifecycle of one local application runtime.

    The window layer sees only ``start`` and ``stop``: port allocation, the
    server thread, the session secret, runtime authorization, the state machine
    and the ordered shutdown all stay inside. ``start`` runs once; a failed
    ``start`` releases whatever it created and lands in ``stopped``. ``stop`` is
    idempotent and always returns a structured report.
    """

    def __init__(self, config: LocalAppHostConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._state = STATE_NEW
        self._app: LocalWebApp | None = None
        self._server: ThreadedWSGIServer | None = None
        self._server_thread: threading.Thread | None = None
        self._gate: SessionGate | None = None
        self._info: LocalAppHostInfo | None = None
        self._runtime: PinnedRuntime | None = None
        self._authorization_error: str | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def start(self) -> LocalAppHostInfo:
        with self._lock:
            if self._state != STATE_NEW:
                raise LocalAppHostError(
                    f"LocalAppHost.start() is not repeatable (state={self._state})"
                )
            self._state = STATE_STARTING

        try:
            return self._start_locked()
        except Exception:
            # Reverse whatever was created and settle in a terminal state.
            self._release_resources()
            with self._lock:
                self._state = STATE_STOPPED
            raise

    def _start_locked(self) -> LocalAppHostInfo:
        library_root = Path(self._config.library_root).expanduser().resolve()
        library_root.mkdir(parents=True, exist_ok=True)

        port = self._config.port or _reserve_ephemeral_port(self._config.host)
        origin_host = f"{self._config.host}:{port}"
        origin = f"http://{origin_host}"
        gate = SessionGate(origin_host=origin_host)

        static_root = self._config.static_root or AppPaths.discover().static_root
        runtime = PinnedRuntime(library_root)
        self._runtime = runtime
        app = create_app(
            str(library_root),
            security=gate,
            static_root=static_root,
            runtime=runtime,
        )
        self._app = app
        self._gate = gate

        server = make_server(
            self._config.host,
            port,
            app,
            server_class=ThreadedWSGIServer,
            handler_class=QuietWSGIRequestHandler,
        )
        self._server = server

        server_thread = threading.Thread(
            target=server.serve_forever,
            name="MemeSortLocalAppHost",
            daemon=True,
        )
        server_thread.start()
        self._server_thread = server_thread

        if self._config.authorize_runtime:
            # Best effort: the window must open even before the runtime is
            # installed so first-run setup can happen inside the UI.
            try:
                runtime.authorize()
            except RuntimeAuthorizationError as exc:
                self._authorization_error = str(exc)

        info = LocalAppHostInfo(
            origin=origin,
            bootstrap_url=gate.bootstrap_url(origin),
            library_root=library_root,
        )
        self._info = info
        with self._lock:
            self._state = STATE_RUNNING
        return info

    def stop(self, timeout: float = 15.0) -> ShutdownReport:
        with self._lock:
            if self._state in (STATE_STOPPED, STATE_NEW):
                self._state = STATE_STOPPED
                return ShutdownReport(authorization_error=self._authorization_error)
            self._state = STATE_STOPPING

        steps: list[ShutdownStep] = []
        clean = True

        app = self._app
        if app is not None:
            steps.append(_timed_step("refuse-new-work", app.begin_shutdown))

        server = self._server
        if server is not None:
            steps.append(_timed_step("stop-http-server", server.shutdown))
            steps.append(_timed_step("close-http-socket", server.server_close))

        if app is not None:
            steps.append(_timed_step("stop-workers", app.shutdown))

        runtime = self._runtime
        if runtime is not None:
            steps.append(_timed_step("close-runtime", runtime.close))

        thread = self._server_thread
        if thread is not None:
            join_step = _timed_join("join-server-thread", thread, timeout)
            steps.append(join_step)
            if join_step.timed_out:
                clean = False

        with self._lock:
            self._state = STATE_STOPPED

        return ShutdownReport(
            steps=tuple(steps),
            clean=clean,
            authorization_error=self._authorization_error,
        )

    def _release_resources(self) -> None:
        server = self._server
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        app = self._app
        if app is not None:
            try:
                app.shutdown()
            except Exception:
                pass
        runtime = self._runtime
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


def _reserve_ephemeral_port(host: str) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _timed_step(name: str, action) -> ShutdownStep:
    started = time.monotonic()
    timed_out = False
    try:
        action()
    except Exception:
        timed_out = True
    return ShutdownStep(name=name, duration_seconds=time.monotonic() - started, timed_out=timed_out)


def _timed_join(name: str, thread: threading.Thread, timeout: float) -> ShutdownStep:
    started = time.monotonic()
    thread.join(timeout=timeout)
    return ShutdownStep(
        name=name,
        duration_seconds=time.monotonic() - started,
        timed_out=thread.is_alive(),
    )
