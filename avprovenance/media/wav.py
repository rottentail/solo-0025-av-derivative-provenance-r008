"""PCM WAV inspection, decoding, and deterministic encoding."""

from __future__ import annotations

import array
import io
import sys
import wave
from dataclasses import dataclass
from typing import Iterable

from ..errors import UnsupportedMedia
from ..models import MediaDescriptor


@dataclass(frozen=True)
class AudioClip:
    sample_rate: int
    channels: tuple[list[int], ...]

    @property
    def frame_count(self) -> int:
        return len(self.channels[0]) if self.channels else 0

    @property
    def channel_count(self) -> int:
        return len(self.channels)


def _open_reader(data: bytes) -> wave.Wave_read:
    try:
        reader = wave.open(io.BytesIO(data), "rb")
    except (EOFError, wave.Error) as exc:
        raise UnsupportedMedia(
            "The audio payload is not a readable RIFF/WAVE file.",
            details={"reason": str(exc)},
        ) from exc
    return reader


def _validate_params(reader: wave.Wave_read) -> None:
    if reader.getcomptype() != "NONE":
        raise UnsupportedMedia(
            "Compressed WAV payloads are not supported.",
            details={"compression": reader.getcomptype()},
        )
    sample_width = reader.getsampwidth()
    if sample_width != 2:
        raise UnsupportedMedia(
            "Only signed 16-bit PCM WAV is supported.",
            details={"sample_width_bytes": sample_width},
        )
    channels = reader.getnchannels()
    if channels not in (1, 2):
        raise UnsupportedMedia(
            "WAV input must contain one or two channels.",
            details={"channels": channels},
        )
    sample_rate = reader.getframerate()
    if sample_rate < 8000 or sample_rate > 192000:
        raise UnsupportedMedia(
            "WAV sample rate is outside the supported range.",
            details={"sample_rate": sample_rate},
        )
    if reader.getnframes() <= 0:
        raise UnsupportedMedia("WAV input has no sample frames.")


def inspect_wav(data: bytes) -> MediaDescriptor:
    reader = _open_reader(data)
    try:
        _validate_params(reader)
        sample_rate = reader.getframerate()
        frame_count = reader.getnframes()
        channels = reader.getnchannels()
        return MediaDescriptor(
            kind="audio",
            mime_type="audio/wav",
            container="wav",
            properties={
                "codec": "pcm_s16le",
                "sample_rate": sample_rate,
                "channels": channels,
                "frame_count": frame_count,
                "duration_seconds": round(frame_count / sample_rate, 9),
            },
        )
    finally:
        reader.close()


def decode_wav(data: bytes) -> AudioClip:
    reader = _open_reader(data)
    try:
        _validate_params(reader)
        channels = reader.getnchannels()
        sample_rate = reader.getframerate()
        raw = reader.readframes(reader.getnframes())
    finally:
        reader.close()

    interleaved = array.array("h")
    interleaved.frombytes(raw)
    if sys.byteorder != "little":
        interleaved.byteswap()
    if len(interleaved) % channels != 0:
        raise UnsupportedMedia("WAV sample data is not frame-aligned.")
    channel_values = tuple(
        interleaved[index::channels].tolist() for index in range(channels)
    )
    return AudioClip(sample_rate=sample_rate, channels=channel_values)


def _interleave(channels: tuple[list[int], ...], frame_count: int) -> list[int]:
    channel_count = len(channels)
    result = [0] * (frame_count * channel_count)
    for channel_index, values in enumerate(channels):
        result[channel_index::channel_count] = values[:frame_count]
    return result


def encode_wav(clip: AudioClip) -> bytes:
    if not clip.channels:
        raise UnsupportedMedia("Cannot encode an audio clip without channels.")
    if any(len(channel) != clip.frame_count for channel in clip.channels):
        raise UnsupportedMedia("Audio channels do not share one frame count.")
    values = array.array("h", _interleave(clip.channels, clip.frame_count))
    if sys.byteorder != "little":
        values.byteswap()
    payload = io.BytesIO()
    with wave.open(payload, "wb") as writer:
        writer.setnchannels(len(clip.channels))
        writer.setsampwidth(2)
        writer.setframerate(clip.sample_rate)
        writer.writeframes(values.tobytes())
    return payload.getvalue()


def replace_channels(
    clip: AudioClip,
    channels: Iterable[list[int]],
) -> AudioClip:
    values = tuple(list(channel) for channel in channels)
    if not values:
        raise UnsupportedMedia("Audio operations produced no channels.")
    expected = len(values[0])
    if any(len(channel) != expected for channel in values):
        raise UnsupportedMedia("Audio operation produced misaligned channels.")
    return AudioClip(sample_rate=clip.sample_rate, channels=values)
