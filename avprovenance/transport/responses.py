"""JSON response writer with a request identifier."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any


def write_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    value: Any,
    *,
    request_id: str,
) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("X-Request-Id", request_id)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(payload)
    except (BrokenPipeError, ConnectionResetError):
        return
