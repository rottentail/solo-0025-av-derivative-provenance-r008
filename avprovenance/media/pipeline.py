"""Route a normalized plan to the matching media implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import UnsupportedMedia
from ..models import MediaDescriptor
from .audio_ops import render_audio
from .errors import MediaOperationFailure
from .inspector import inspect_media
from .video_ops import render_video


@dataclass(frozen=True)
class RenderOutcome:
    data: bytes
    descriptor: MediaDescriptor
    operation_types: list[str]


def apply_media_operations(
    primary_bytes: bytes,
    operations: list[dict[str, Any]],
    secondary_bytes: bytes | None,
) -> RenderOutcome:
    primary = inspect_media(primary_bytes)
    if secondary_bytes is not None:
        secondary = inspect_media(secondary_bytes)
        if secondary.kind != primary.kind:
            raise MediaOperationFailure(
                "MEDIA_KIND_MISMATCH",
                "Primary and secondary media must share a media kind.",
            )

    if primary.kind == "audio":
        output = render_audio(primary_bytes, operations, secondary_bytes)
    elif primary.kind == "video":
        output = render_video(primary_bytes, operations, secondary_bytes)
    else:
        raise UnsupportedMedia("The primary media kind has no renderer.")

    descriptor = inspect_media(output)
    if descriptor.kind != primary.kind:
        raise UnsupportedMedia("The renderer changed the media kind unexpectedly.")
    return RenderOutcome(
        data=output,
        descriptor=descriptor,
        operation_types=[str(operation["type"]) for operation in operations],
    )
