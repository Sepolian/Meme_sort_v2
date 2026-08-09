from __future__ import annotations

import http.cookiejar
import io
import json
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from memesort_worker.local_app_host import LocalAppHost, ShutdownReport, STATE_STOPPED
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.sidecar_entry import PROTOCOL_VERSION, SidecarHandshake, main


class _ControlPipe:
    """A blocking text input whose ``None`` value simulates stdin EOF."""

    def __init__(self) -> None:
        self._lines: queue.Queue[str | None] = queue.Queue()

    def send(self, line: str) -> None:
        self._lines.put(line)

    def close(self) -> None:
        self._lines.put(None)

    def __iter__(self):
        while True:
            line = self._lines.get(timeout=5)
            if line is None:
                return
            yield line


class _NotifyingOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.written = threading.Event()

    def write(self, value: str) -> int:
        result = super().write(value)
        self.written.set()
        return result


def _test_host_factory(config):
    return LocalAppHost(replace(config, authorize_runtime=False))


class SidecarEntryTests(unittest.TestCase):
    def test_portable_root_rehomes_manifest_artifacts_for_the_entire_sidecar_session(self) -> None:
        class StubHost:
            def start(self):
                return type(
                    "Info",
                    (),
                    {
                        "origin": "http://127.0.0.1:1234",
                        "bootstrap_url": "http://127.0.0.1:1234/?bootstrap=test",
                        "library_root": Path("C:/library"),
                    },
                )()

            def stop(self):
                return ShutdownReport()

        with tempfile.TemporaryDirectory() as temp_dir:
            portable_root = Path(temp_dir) / "MemeSort-portable"
            observed: list[Path] = []
            result = main(
                ["--portable-root", str(portable_root)],
                stdin=io.StringIO(),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                host_factory=lambda _config: (
                    observed.append(load_runtime_manifest().model_install_dir) or StubHost()
                ),
            )

        self.assertEqual(0, result)
        self.assertEqual(
            [portable_root.resolve() / "MemeSortData" / "models" / "gguf" / "qwen3-2b-q4_k_m"],
            observed,
        )

    def _start_sidecar(self, library_root: Path):
        control = _ControlPipe()
        stdout = _NotifyingOutput()
        stderr = io.StringIO()
        result: list[int] = []
        thread = threading.Thread(
            target=lambda: result.append(
                main(
                    ["--library-root", str(library_root)],
                    stdin=control,
                    stdout=stdout,
                    stderr=stderr,
                    host_factory=_test_host_factory,
                )
            ),
            daemon=True,
        )
        thread.start()
        self.assertTrue(stdout.written.wait(timeout=5), "sidecar did not emit a handshake")
        handshake = json.loads(stdout.getvalue())
        return control, stdout, stderr, result, thread, handshake

    def test_handshake_uses_dynamic_port_and_bootstrap_is_one_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                control,
                stdout,
                _stderr,
                result,
                thread,
                handshake,
            ) = self._start_sidecar(Path(temp_dir) / "library")
            try:
                self.assertEqual(PROTOCOL_VERSION, handshake["protocol_version"])
                self.assertTrue(handshake["origin"].startswith("http://127.0.0.1:"))
                self.assertNotEqual("http://127.0.0.1:0", handshake["origin"])
                self.assertTrue(handshake["bootstrap_url"].startswith(handshake["origin"]))

                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
                )
                with opener.open(handshake["bootstrap_url"], timeout=5) as response:
                    self.assertIn(b'id="searchNavGroup"', response.read())
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(handshake["bootstrap_url"], timeout=5)
                self.assertEqual(403, ctx.exception.code)
            finally:
                control.send('{"command":"shutdown"}\n')
                thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual([0], result)
            self.assertEqual(1, len(stdout.getvalue().splitlines()))

    def test_eof_stops_the_host_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control, _stdout, _stderr, result, thread, _handshake = self._start_sidecar(
                Path(temp_dir) / "library"
            )
            control.close()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual([0], result)

    def test_explicit_log_directory_receives_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = main(
                ["--library-root", str(root / "library"), "--log-dir", str(root / "logs")],
                stdin=io.StringIO(),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                host_factory=_test_host_factory,
            )
            log_contents = (root / "logs" / "sidecar.log").read_text(encoding="utf-8")

        self.assertEqual(0, result)
        self.assertIn("sidecar_started", log_contents)
        self.assertIn("sidecar_stopped clean=True", log_contents)

    def test_start_failure_never_emits_a_handshake_and_stops_the_host(self) -> None:
        class FailingHost:
            def __init__(self) -> None:
                self.stop_calls = 0

            def start(self):
                raise OSError("port unavailable")

            def stop(self):
                self.stop_calls += 1
                return ShutdownReport()

        host = FailingHost()
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = main(
            [],
            stdin=io.StringIO(),
            stdout=stdout,
            stderr=stderr,
            host_factory=lambda _config: host,
        )

        self.assertEqual(1, result)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual(1, host.stop_calls)
        self.assertIn("failed to start", stderr.getvalue())

    def test_actual_start_failure_releases_the_host_runtime(self) -> None:
        created: list[LocalAppHost] = []

        def factory(config):
            host = LocalAppHost(replace(config, authorize_runtime=False))
            created.append(host)
            return host

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "memesort_worker.local_app_host.make_server",
            side_effect=OSError("port unavailable"),
        ):
            result = main(
                ["--library-root", str(Path(temp_dir) / "library")],
                stdin=io.StringIO(),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                host_factory=factory,
            )

        self.assertEqual(1, result)
        self.assertEqual(STATE_STOPPED, created[0].state)
        self.assertTrue(created[0]._runtime.closed)

    def test_handshake_schema_is_versioned(self) -> None:
        handshake = SidecarHandshake(
            protocol_version=PROTOCOL_VERSION,
            origin="http://127.0.0.1:1234",
            bootstrap_url="http://127.0.0.1:1234/?bootstrap=secret",
            library_root="C:/library",
        )

        self.assertEqual(PROTOCOL_VERSION, handshake.protocol_version)
        self.assertEqual("C:/library", handshake.library_root)


if __name__ == "__main__":
    unittest.main()
