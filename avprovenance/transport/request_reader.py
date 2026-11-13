"""Bounded JSON request body reader."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any

from ..errors import ValidationFailure
from ..validation import MAX_HTTP_BODY_BYTES, require_object


def read_json_body(
    handler: BaseHTTPRequestHandler,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    raw_length = handler.headers.get("Content-Length")
    if raw_length is None:
        if required:
            raise ValidationFailure("Content-Length is required for this request.")
        return None
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ValidationFailure("Content-Length must be an integer.") from exc
    if length < 0 or length > MAX_HTTP_BODY_BYTES:
        raise ValidationFailure(
            "Request body exceeds the configured limit.",
            details={"max_bytes": MAX_HTTP_BODY_BYTES},
        )
    if length == 0:
        if required:
            raise ValidationFailure("A JSON request body is required.")
        return None
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValidationFailure("Request body ended before Content-Length.")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("Request body must be valid UTF-8 JSON.") from exc
    return require_object(value, "request")
