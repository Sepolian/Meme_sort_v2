from __future__ import annotations

import json
from io import BytesIO


def call(app, method, path, *, query="", headers=None, body=b"", host=None):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
    }
    if host is not None:
        environ["HTTP_HOST"] = host
    environ.update(headers or {})
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = response_headers

    payload = b"".join(app(environ, start_response))
    return str(captured["status"]), dict(captured["headers"]), payload


def call_json(app, method, path, *, query="", headers=None, payload=None, host=None):
    body = json.dumps(payload or {}).encode("utf-8")
    status, _headers, response_body = call(
        app, method, path, query=query, headers=headers, body=body, host=host
    )
    return status, json.loads(response_body)
