"""Execute a normalized plan and convert media failures to domain errors."""

from __future__ import annotations

from typing import Any

from ..errors import RenderFailed, UnsupportedMedia
from ..media import MediaOperationFailure, RenderOutcome, apply_media_operations


def execute_plan(
    primary_bytes: bytes,
    operations: list[dict[str, Any]],
    secondary_bytes: bytes | None,
) -> RenderOutcome:
    try:
        return apply_media_operations(primary_bytes, operations, secondary_bytes)
    except MediaOperationFailure as exc:
        raise RenderFailed(
            "The rendition could not be produced.",
            details={"reason": exc.code, "detail": exc.message},
        ) from exc
    except UnsupportedMedia as exc:
        raise RenderFailed(
            "The rendition produced unsupported media bytes.",
            details={"reason": exc.code},
        ) from exc
