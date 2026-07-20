from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from typing import Mapping


SESSION_COOKIE_NAME = "memesort_session"
BOOTSTRAP_PARAM = "bootstrap"

# Applied to every response the gate authorizes. The UI ships one external
# script and stylesheet and only loads same-origin media, so a tight policy
# does not require any inline allowances beyond styles the browser injects.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)


@dataclass(frozen=True)
class GateOutcome:
    """The gate's verdict for one loopback request."""

    action: str  # "allow" | "reject" | "bootstrap"
    status: HTTPStatus = HTTPStatus.OK
    detail: str = ""
    set_cookie: str | None = None
    location: str | None = None


class SessionGate:
    """Authenticate loopback requests for one application session.

    Binding to ``127.0.0.1`` is not enough: any local process or web page could
    reach the API. The gate requires a per-session cookie, established through a
    one-time bootstrap secret that the window loads first, and rejects requests
    with a foreign ``Host`` or cross-origin mutation ``Origin``.
    """

    def __init__(
        self,
        *,
        origin_host: str,
        bootstrap_secret: str | None = None,
        session_token: str | None = None,
    ) -> None:
        self._origin_host = origin_host
        self._origin = f"http://{origin_host}"
        self._bootstrap_secret = bootstrap_secret or secrets.token_urlsafe(32)
        self._session_token = session_token or secrets.token_urlsafe(32)
        self._bootstrap_consumed = False
        self._lock = threading.Lock()

    @property
    def bootstrap_secret(self) -> str:
        return self._bootstrap_secret

    def bootstrap_url(self, origin: str | None = None) -> str:
        base = origin or self._origin
        return f"{base}/?{BOOTSTRAP_PARAM}={self._bootstrap_secret}"

    def evaluate(
        self,
        method: str,
        query: Mapping[str, list[str]],
        headers: Mapping[str, str],
    ) -> GateOutcome:
        host = headers.get("host", "")
        if host != self._origin_host:
            return GateOutcome("reject", HTTPStatus.FORBIDDEN, "Invalid Host header")

        if method not in ("GET", "HEAD"):
            origin = headers.get("origin")
            if origin is not None and origin != self._origin:
                return GateOutcome("reject", HTTPStatus.FORBIDDEN, "Invalid Origin header")

        supplied = _first_query_value(query, BOOTSTRAP_PARAM)
        if supplied is not None:
            return self._consume_bootstrap(supplied)

        if self._has_valid_session(headers):
            return GateOutcome("allow")
        return GateOutcome("reject", HTTPStatus.UNAUTHORIZED, "Session required")

    def _consume_bootstrap(self, supplied: str) -> GateOutcome:
        with self._lock:
            already_used = self._bootstrap_consumed
            matches = secrets.compare_digest(supplied, self._bootstrap_secret)
            if already_used or not matches:
                return GateOutcome(
                    "reject",
                    HTTPStatus.FORBIDDEN,
                    "Invalid or already used bootstrap token",
                )
            self._bootstrap_consumed = True
        return GateOutcome(
            "bootstrap",
            HTTPStatus.SEE_OTHER,
            set_cookie=self._session_cookie_header(),
            location="/",
        )

    def _session_cookie_header(self) -> str:
        cookie: SimpleCookie = SimpleCookie()
        cookie[SESSION_COOKIE_NAME] = self._session_token
        morsel = cookie[SESSION_COOKIE_NAME]
        morsel["httponly"] = True
        morsel["samesite"] = "Strict"
        morsel["path"] = "/"
        return morsel.OutputString()

    def _has_valid_session(self, headers: Mapping[str, str]) -> bool:
        raw = headers.get("cookie")
        if not raw:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except CookieError:
            return False
        morsel = jar.get(SESSION_COOKIE_NAME)
        if morsel is None:
            return False
        return secrets.compare_digest(morsel.value, self._session_token)


def request_headers_from_environ(environ: Mapping[str, object]) -> dict[str, str]:
    """Extract the header subset the gate needs from a WSGI environ."""
    headers: dict[str, str] = {}
    for source, target in (("HTTP_HOST", "host"), ("HTTP_ORIGIN", "origin"), ("HTTP_COOKIE", "cookie")):
        value = environ.get(source)
        if value is not None:
            headers[target] = str(value)
    return headers


def _first_query_value(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]
