from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from memesort_worker.web_security import SESSION_COOKIE_NAME, SessionGate
from memesort_worker.webapp import create_app


ORIGIN_HOST = "127.0.0.1:8765"
ORIGIN = f"http://{ORIGIN_HOST}"
BOOTSTRAP_SECRET = "bootstrap-secret-value"
SESSION_TOKEN = "session-token-value"


def _call(app, method, path, *, query="", headers=None, body=b""):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
        "HTTP_HOST": ORIGIN_HOST,
    }
    for key, value in (headers or {}).items():
        environ[key] = value
    captured: dict[str, object] = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = response_headers

    payload = b"".join(app(environ, start_response))
    header_map = {name: value for name, value in captured["headers"]}
    return str(captured["status"]), header_map, payload


class WebSecurityTests(unittest.TestCase):
    def _make_app(self, temp_dir):
        gate = SessionGate(
            origin_host=ORIGIN_HOST,
            bootstrap_secret=BOOTSTRAP_SECRET,
            session_token=SESSION_TOKEN,
        )
        app = create_app(str(Path(temp_dir) / "library"), security=gate)
        return app, gate

    def _session_headers(self):
        return {"HTTP_COOKIE": f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"}

    def test_api_without_session_is_unauthorized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                status, _headers, _body = _call(app, "GET", "/api/state")
                self.assertTrue(status.startswith("401 "))
            finally:
                app.shutdown()

    def test_media_without_session_is_unauthorized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                status, _headers, _body = _call(app, "GET", "/media/anything.png")
                self.assertTrue(status.startswith("401 "))
            finally:
                app.shutdown()

    def test_bootstrap_sets_session_cookie_and_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                status, headers, _body = _call(
                    app, "GET", "/", query=f"bootstrap={BOOTSTRAP_SECRET}"
                )
                self.assertTrue(status.startswith("303 "))
                self.assertEqual(headers.get("Location"), "/")
                set_cookie = headers.get("Set-Cookie", "")
                self.assertIn(f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}", set_cookie)
                self.assertIn("HttpOnly", set_cookie)
                self.assertIn("SameSite=Strict", set_cookie)
            finally:
                app.shutdown()

    def test_bootstrap_token_cannot_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                first, _headers, _body = _call(
                    app, "GET", "/", query=f"bootstrap={BOOTSTRAP_SECRET}"
                )
                self.assertTrue(first.startswith("303 "))
                second, _headers2, _body2 = _call(
                    app, "GET", "/", query=f"bootstrap={BOOTSTRAP_SECRET}"
                )
                self.assertTrue(second.startswith("403 "))
            finally:
                app.shutdown()

    def test_valid_session_reaches_the_api_with_security_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                status, headers, _body = _call(
                    app, "GET", "/api/state", headers=self._session_headers()
                )
                self.assertTrue(status.startswith("200 "))
                self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))
                self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
            finally:
                app.shutdown()

    def test_foreign_host_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                status, _headers, _body = _call(
                    app,
                    "GET",
                    "/api/state",
                    headers={"HTTP_HOST": "evil.example", **self._session_headers()},
                )
                self.assertTrue(status.startswith("403 "))
            finally:
                app.shutdown()

    def test_cross_origin_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                status, _headers, _body = _call(
                    app,
                    "POST",
                    "/api/import-folder",
                    body=b"{}",
                    headers={
                        "HTTP_ORIGIN": "http://evil.example",
                        **self._session_headers(),
                    },
                )
                self.assertTrue(status.startswith("403 "))
            finally:
                app.shutdown()

    def test_same_origin_mutation_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                # Missing "path" makes the handler fail with 400, which still proves
                # the request cleared the gate rather than being rejected at 403/401.
                status, _headers, _body = _call(
                    app,
                    "POST",
                    "/api/import-folder",
                    body=b"{}",
                    headers={"HTTP_ORIGIN": ORIGIN, **self._session_headers()},
                )
                self.assertTrue(status.startswith("400 "))
            finally:
                app.shutdown()

    def test_mutations_are_refused_once_shutdown_begins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app, _ = self._make_app(temp_dir)
            try:
                app.begin_shutdown()
                status, _headers, _body = _call(
                    app,
                    "POST",
                    "/api/import-folder",
                    body=b"{}",
                    headers={"HTTP_ORIGIN": ORIGIN, **self._session_headers()},
                )
                self.assertTrue(status.startswith("503 "))
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
