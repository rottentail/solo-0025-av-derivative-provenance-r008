"""Resolve media bytes to a supported inspector and descriptor."""

from __future__ import annotations

from ..errors import UnsupportedMedia
from ..models import MediaDescriptor
from .wav import inspect_wav
from .y4m import inspect_y4m


def inspect_media(data: bytes) -> MediaDescriptor:
    if not data:
        raise UnsupportedMedia("The media payload is empty.")
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return inspect_wav(data)
    if data.startswith(b"YUV4MPEG2"):
        return inspect_y4m(data)
    raise UnsupportedMedia(
        "The payload is not a supported PCM WAV or planar Y4M stream.",
        details={"prefix_hex": data[:12].hex()},
    )
