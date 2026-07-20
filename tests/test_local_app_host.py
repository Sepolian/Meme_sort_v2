from __future__ import annotations

import http.cookiejar
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from memesort_worker.local_app_host import (
    STATE_NEW,
    STATE_RUNNING,
    STATE_STOPPED,
    LocalAppHost,
    LocalAppHostConfig,
    LocalAppHostError,
)


def _config(temp_dir: str) -> LocalAppHostConfig:
    return LocalAppHostConfig(
        library_root=Path(temp_dir) / "library",
        authorize_runtime=False,
    )


class LocalAppHostTests(unittest.TestCase):
    def test_start_binds_a_random_port_and_reports_bootstrap_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(_config(temp_dir))
            self.assertEqual(host.state, STATE_NEW)
            try:
                info = host.start()
                self.assertEqual(host.state, STATE_RUNNING)
                self.assertTrue(info.origin.startswith("http://127.0.0.1:"))
                self.assertNotEqual(info.origin, "http://127.0.0.1:0")
                self.assertIn("bootstrap=", info.bootstrap_url)
                self.assertTrue(info.bootstrap_url.startswith(info.origin))
            finally:
                host.stop()

    def test_bootstrap_url_serves_the_app_shell_over_the_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(_config(temp_dir))
            info = host.start()
            try:
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
                )
                with opener.open(info.bootstrap_url, timeout=5) as response:
                    body = response.read()
                    final_url = response.geturl()
                # The bootstrap redirect lands on the shell, which carries the nav group.
                self.assertIn(b'id="searchNavGroup"', body)
                self.assertNotIn("bootstrap=", final_url)
            finally:
                host.stop()

    def test_unauthenticated_request_cannot_read_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(_config(temp_dir))
            info = host.start()
            try:
                request = urllib.request.Request(f"{info.origin}/api/state")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(ctx.exception.code, 401)
            finally:
                host.stop()

    def test_start_is_not_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(_config(temp_dir))
            host.start()
            try:
                with self.assertRaises(LocalAppHostError):
                    host.start()
            finally:
                host.stop()

    def test_stop_is_idempotent_and_reports_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(_config(temp_dir))
            host.start()
            first = host.stop()
            second = host.stop()

            self.assertTrue(first.clean)
            self.assertEqual(host.state, STATE_STOPPED)
            self.assertTrue(second.clean)
            step_names = {step.name for step in first.steps}
            self.assertIn("stop-http-server", step_names)
            self.assertIn("join-server-thread", step_names)

    def test_stop_releases_the_listening_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(_config(temp_dir))
            info = host.start()
            host.stop()

            request = urllib.request.Request(f"{info.origin}/api/state")
            with self.assertRaises(urllib.error.URLError):
                urllib.request.urlopen(request, timeout=2)

    def test_repeated_start_stop_cycles_do_not_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for _ in range(5):
                host = LocalAppHost(_config(temp_dir))
                info = host.start()
                self.assertTrue(info.origin.startswith("http://127.0.0.1:"))
                report = host.stop()
                self.assertTrue(report.clean)


if __name__ == "__main__":
    unittest.main()
