"""Planar Y4M operations that preserve deterministic frame layout."""

from __future__ import annotations

from typing import Any

from .errors import MediaOperationFailure
from .y4m import Y4mClip, Y4mFrame, decode_y4m, encode_y4m


def trim_video(clip: Y4mClip, operation: dict[str, Any]) -> Y4mClip:
    start = int(operation["start_frame"])
    count = int(operation["frame_count"])
    if start < 0 or start >= clip.frame_count:
        raise MediaOperationFailure(
            "VIDEO_TRIM_OUT_OF_RANGE",
            "The requested video trim starts outside the source.",
        )
    end = min(clip.frame_count, start + max(1, count))
    frames = clip.frames[start:end]
    if not frames:
        raise MediaOperationFailure(
            "VIDEO_TRIM_EMPTY",
            "The requested video trim produces no frames.",
        )
    return Y4mClip(
        width=clip.width,
        height=clip.height,
        rate_numerator=clip.rate_numerator,
        rate_denominator=clip.rate_denominator,
        interlace=clip.interlace,
        pixel_format=clip.pixel_format,
        frames=frames,
    )


def _crop_plane(
    plane: bytes,
    plane_width: int,
    x: int,
    y: int,
    width: int,
    height: int,
) -> bytes:
    rows = []
    for row_index in range(y, y + height):
        start = row_index * plane_width + x
        rows.append(plane[start : start + width])
    return b"".join(rows)


def crop_video(clip: Y4mClip, operation: dict[str, Any]) -> Y4mClip:
    x = int(operation["x"])
    y = int(operation["y"])
    width = int(operation["width"])
    height = int(operation["height"])
    if any(value % 2 for value in (x, y, width, height)):
        raise MediaOperationFailure(
            "VIDEO_CROP_ALIGNMENT",
            "Video crop geometry must align to even pixels.",
        )
    if width < 2 or height < 2:
        raise MediaOperationFailure(
            "VIDEO_CROP_TOO_SMALL",
            "Crop width and height must be at least two pixels.",
        )
    if x + width > clip.width or y + height > clip.height:
        raise MediaOperationFailure(
            "VIDEO_CROP_OUT_OF_RANGE",
            "Crop geometry exceeds the source frame.",
        )

    chroma_x = x // 2
    chroma_y = y // 2
    chroma_width = width // 2
    chroma_height = height // 2
    frames = []
    for frame in clip.frames:
        frames.append(
            Y4mFrame(
                y_plane=_crop_plane(
                    frame.y_plane, clip.width, x, y, width, height
                ),
                u_plane=_crop_plane(
                    frame.u_plane,
                    clip.chroma_width,
                    chroma_x,
                    chroma_y,
                    chroma_width,
                    chroma_height,
                ),
                v_plane=_crop_plane(
                    frame.v_plane,
                    clip.chroma_width,
                    chroma_x,
                    chroma_y,
                    chroma_width,
                    chroma_height,
                ),
            )
        )
    return Y4mClip(
        width=width,
        height=height,
        rate_numerator=clip.rate_numerator,
        rate_denominator=clip.rate_denominator,
        interlace=clip.interlace,
        pixel_format=clip.pixel_format,
        frames=tuple(frames),
    )


def offset_luma(clip: Y4mClip, operation: dict[str, Any]) -> Y4mClip:
    amount = int(operation["amount"])
    frames = []
    for frame in clip.frames:
        adjusted = bytes(
            max(0, min(255, value + amount))
            for value in frame.y_plane
        )
        frames.append(
            Y4mFrame(
                y_plane=adjusted,
                u_plane=frame.u_plane,
                v_plane=frame.v_plane,
            )
        )
    return Y4mClip(
        width=clip.width,
        height=clip.height,
        rate_numerator=clip.rate_numerator,
        rate_denominator=clip.rate_denominator,
        interlace=clip.interlace,
        pixel_format=clip.pixel_format,
        frames=tuple(frames),
    )


def _mirror_plane(plane: bytes, width: int, height: int) -> bytes:
    rows = []
    for row_index in range(height):
        start = row_index * width
        row = plane[start : start + width]
        rows.append(row[::-1])
    return b"".join(rows)


def mirror_video(clip: Y4mClip, operation: dict[str, Any]) -> Y4mClip:
    del operation
    frames = tuple(
        Y4mFrame(
            y_plane=_mirror_plane(frame.y_plane, clip.width, clip.height),
            u_plane=_mirror_plane(
                frame.u_plane, clip.chroma_width, clip.chroma_height
            ),
            v_plane=_mirror_plane(
                frame.v_plane, clip.chroma_width, clip.chroma_height
            ),
        )
        for frame in clip.frames
    )
    return Y4mClip(
        width=clip.width,
        height=clip.height,
        rate_numerator=clip.rate_numerator,
        rate_denominator=clip.rate_denominator,
        interlace=clip.interlace,
        pixel_format=clip.pixel_format,
        frames=frames,
    )


def render_video(
    primary_bytes: bytes,
    operations: list[dict[str, Any]],
    secondary_bytes: bytes | None,
) -> bytes:
    if secondary_bytes is not None:
        raise MediaOperationFailure(
            "VIDEO_SECONDARY_UNSUPPORTED",
            "The current video operation set accepts one source.",
        )
    clip = decode_y4m(primary_bytes)
    try:
        for operation in operations:
            operation_type = operation["type"]
            if operation_type == "media_trim":
                clip = trim_video(clip, operation)
            elif operation_type == "video_crop":
                clip = crop_video(clip, operation)
            elif operation_type == "video_luma_offset":
                clip = offset_luma(clip, operation)
            elif operation_type == "video_mirror":
                clip = mirror_video(clip, operation)
            else:
                raise MediaOperationFailure(
                    "UNSUPPORTED_VIDEO_OPERATION",
                    f"Unsupported video operation: {operation_type}",
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaOperationFailure(
            "VIDEO_OPERATION_INVALID",
            "The video operation payload is malformed.",
        ) from exc
    return encode_y4m(clip)
