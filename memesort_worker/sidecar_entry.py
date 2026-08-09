"""Headless Python sidecar for the Tauri desktop host.

The WebView never receives the bootstrap URL or the authenticated loopback
cookie. Tauri reads the single JSON handshake emitted on stdout, consumes the
bootstrap URL itself, and then proxies an explicit allowlist of commands.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Iterable, TextIO

from .app_paths import AppPaths
from .local_app_host import (
    LocalAppHost,
    LocalAppHostConfig,
    LocalAppHostInfo,
)


PROTOCOL_VERSION = 1
_LOG = logging.getLogger("memesort.sidecar")
_LOG.addHandler(logging.NullHandler())
_LOG.propagate = False


@dataclass(frozen=True)
class SidecarHandshake:
    """The one and only successful stdout message from a sidecar process."""

    protocol_version: int
    origin: str
    bootstrap_url: str
    library_root: str

    @classmethod
    def from_host_info(cls, info: LocalAppHostInfo) -> "SidecarHandshake":
        return cls(
            protocol_version=PROTOCOL_VERSION,
            origin=info.origin,
            bootstrap_url=info.bootstrap_url,
            library_root=str(info.library_root),
        )


HostFactory = Callable[[LocalAppHostConfig], LocalAppHost]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memesort-sidecar")
    parser.add_argument(
        "--library-root",
        default=None,
        help="Library Root. Defaults to AppData\\Roaming\\MemeSort.",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Optional directory for rotating sidecar lifecycle logs.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    stdin: Iterable[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    host_factory: HostFactory = LocalAppHost,
) -> int:
    """Run a sidecar until Tauri sends ``shutdown`` or closes stdin.

    ``stdout`` intentionally carries only the versioned handshake. Diagnostics
    go to stderr so the Rust parent can read the first line without parsing log
    noise. When requested, lifecycle diagnostics are also written to a rotating
    UTF-8 log file under ``--log-dir``.
    """

    args = build_parser().parse_args(argv)
    protocol_stdin = stdin if stdin is not None else sys.stdin
    protocol_stdout = stdout if stdout is not None else sys.stdout
    protocol_stderr = stderr if stderr is not None else sys.stderr
    _configure_protocol_encoding(protocol_stdout)
    log_handler = _configure_log_handler(args.log_dir, protocol_stderr)

    library_root = (
        Path(args.library_root).expanduser().resolve()
        if args.library_root
        else AppPaths.discover().default_library_root
    )
    host = host_factory(LocalAppHostConfig(library_root=library_root))

    try:
        info = host.start()
    except Exception as exc:
        # LocalAppHost cleans partial state on start failure; calling stop keeps
        # that guarantee true for alternate host factories used by integration
        # harnesses as well.
        host.stop()
        _LOG.exception("sidecar_start_failed")
        _write_stderr(protocol_stderr, f"MemeSort sidecar failed to start: {exc}")
        _remove_log_handler(log_handler)
        return 1

    try:
        _write_handshake(protocol_stdout, SidecarHandshake.from_host_info(info))
        _LOG.info("sidecar_started library_root=%s", info.library_root)
        _wait_for_shutdown(protocol_stdin, protocol_stderr)
    finally:
        report = host.stop()
        _LOG.info("sidecar_stopped clean=%s", report.clean)
        _remove_log_handler(log_handler)

    if not report.clean:
        _write_stderr(protocol_stderr, "MemeSort sidecar did not shut down cleanly.")
        return 2
    return 0


def _wait_for_shutdown(stdin: Iterable[str], stderr: TextIO) -> None:
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write_stderr(stderr, "Ignoring malformed sidecar control message.")
            continue
        if not isinstance(message, dict) or message.get("command") != "shutdown":
            _write_stderr(stderr, "Ignoring unknown sidecar control message.")
            continue
        return
    # EOF is an intentional shutdown signal from the Tauri parent.


def _write_handshake(stdout: TextIO, handshake: SidecarHandshake) -> None:
    stdout.write(json.dumps(asdict(handshake), separators=(",", ":")) + "\n")
    stdout.flush()


def _write_stderr(stderr: TextIO, message: str) -> None:
    stderr.write(message + "\n")
    stderr.flush()


def _configure_protocol_encoding(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="strict", newline="\n", write_through=True)


def _configure_log_handler(log_dir: str | None, stderr: TextIO) -> logging.Handler | None:
    if not log_dir:
        return None
    try:
        resolved_log_dir = Path(log_dir).expanduser().resolve()
        resolved_log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            resolved_log_dir / "sidecar.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as exc:
        _write_stderr(stderr, f"MemeSort sidecar could not initialize logs: {exc}")
        return None

    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False
    return handler


def _remove_log_handler(handler: logging.Handler | None) -> None:
    if handler is None:
        return
    _LOG.removeHandler(handler)
    handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
