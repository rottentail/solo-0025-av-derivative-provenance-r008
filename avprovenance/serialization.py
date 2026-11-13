"""Small deterministic serialization helpers used by every layer."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_json(value: Any) -> str:
    return sha256_hex(canonical_json_bytes(value))


def deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))
