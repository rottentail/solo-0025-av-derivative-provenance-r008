"""Validate and normalize media plans before any lifecycle mutation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..errors import PlanInvalid
from ..serialization import digest_json
from ..validation import (
    require_integer,
    require_number,
    require_object,
    require_string,
)


MAX_OPERATIONS = 12


@dataclass(frozen=True)
class CompiledPlan:
    operations: list[dict[str, Any]]
    digest: str
    operation_types: list[str]


def _only_keys(operation: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(operation) - allowed)
    if unknown:
        raise PlanInvalid(
            f"{name} contains unsupported fields.",
            details={"fields": unknown},
        )


def _audio_trim(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(
        operation,
        {"type", "start_seconds", "duration_seconds"},
        "media_trim",
    )
    return {
        "type": "media_trim",
        "start_seconds": require_number(
            operation.get("start_seconds"),
            "start_seconds",
            minimum=0,
            maximum=600,
        ),
        "duration_seconds": require_number(
            operation.get("duration_seconds"),
            "duration_seconds",
            minimum=0.001,
            maximum=600,
        ),
    }


def _audio_gain(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(operation, {"type", "db"}, "audio_gain")
    return {
        "type": "audio_gain",
        "db": require_number(
            operation.get("db"),
            "db",
            minimum=-48,
            maximum=24,
        ),
    }


def _audio_normalize(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(operation, {"type", "target_peak"}, "audio_normalize")
    return {
        "type": "audio_normalize",
        "target_peak": require_number(
            operation.get("target_peak"),
            "target_peak",
            minimum=0.05,
            maximum=1.0,
        ),
    }


def _audio_mix(
    operation: dict[str, Any],
    *,
    has_secondary: bool,
) -> dict[str, Any]:
    _only_keys(
        operation,
        {
            "type",
            "secondary_gain",
            "primary_weight",
            "secondary_weight",
        },
        "audio_mix",
    )
    if not has_secondary:
        raise PlanInvalid("audio_mix requires secondary_asset_id.")
    return {
        "type": "audio_mix",
        "secondary_gain": require_number(
            operation.get("secondary_gain"),
            "secondary_gain",
            minimum=-48,
            maximum=24,
        ),
        "primary_weight": require_number(
            operation.get("primary_weight"),
            "primary_weight",
            minimum=0,
            maximum=1.5,
        ),
        "secondary_weight": require_number(
            operation.get("secondary_weight"),
            "secondary_weight",
            minimum=0,
            maximum=1.5,
        ),
    }


def _video_trim(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(
        operation,
        {"type", "start_frame", "frame_count"},
        "media_trim",
    )
    return {
        "type": "media_trim",
        "start_frame": require_integer(
            operation.get("start_frame"),
            "start_frame",
            minimum=0,
            maximum=239,
        ),
        "frame_count": require_integer(
            operation.get("frame_count"),
            "frame_count",
            minimum=1,
            maximum=240,
        ),
    }


def _video_crop(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(
        operation,
        {"type", "x", "y", "width", "height"},
        "video_crop",
    )
    values = {
        "type": "video_crop",
        "x": require_integer(operation.get("x"), "x", minimum=0, maximum=1918),
        "y": require_integer(operation.get("y"), "y", minimum=0, maximum=1078),
        "width": require_integer(
            operation.get("width"),
            "width",
            minimum=2,
            maximum=1920,
        ),
        "height": require_integer(
            operation.get("height"),
            "height",
            minimum=2,
            maximum=1080,
        ),
    }
    if any(values[key] % 2 for key in ("x", "y", "width", "height")):
        raise PlanInvalid("video_crop geometry must use even values.")
    return values


def _video_luma(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(operation, {"type", "amount"}, "video_luma_offset")
    return {
        "type": "video_luma_offset",
        "amount": require_integer(
            operation.get("amount"),
            "amount",
            minimum=-64,
            maximum=64,
        ),
    }


def _video_mirror(operation: dict[str, Any]) -> dict[str, Any]:
    _only_keys(operation, {"type"}, "video_mirror")
    return {"type": "video_mirror"}


def _operation_validator(
    operation_type: str,
    media_kind: str,
) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    if media_kind == "audio":
        validators = {
            "media_trim": _audio_trim,
            "audio_gain": _audio_gain,
            "audio_normalize": _audio_normalize,
            "audio_mix": _audio_mix,
            "video_crop": None,
        }
    else:
        validators = {
            "media_trim": _video_trim,
            "video_crop": _video_crop,
            "video_luma_offset": _video_luma,
            "video_mirror": _video_mirror,
        }
    return validators.get(operation_type)


def compile_plan(
    media_kind: str,
    raw_operations: Any,
    *,
    has_secondary: bool,
) -> CompiledPlan:
    if media_kind not in {"audio", "video"}:
        raise PlanInvalid("The primary media kind is not supported.")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise PlanInvalid("operations must be a non-empty array.")
    if len(raw_operations) > MAX_OPERATIONS:
        raise PlanInvalid(
            "The plan contains too many operations.",
            details={"maximum": MAX_OPERATIONS},
        )

    normalized: list[dict[str, Any]] = []
    mixed = False
    for index, raw in enumerate(raw_operations):
        operation = require_object(raw, f"operations[{index}]")
        operation_type = require_string(
            operation.get("type"),
            f"operations[{index}].type",
            maximum=64,
        )
        if media_kind == "audio" and operation_type == "audio_mix":
            if mixed:
                raise PlanInvalid("audio_mix may appear only once.")
            normalized.append(_audio_mix(operation, has_secondary=has_secondary))
            mixed = True
            continue

        validator = _operation_validator(operation_type, media_kind)
        if validator is None:
            raise PlanInvalid(
                f"Operation {operation_type!r} is not valid for {media_kind} media.",
                details={"index": index},
            )
        normalized.append(validator(operation))

    if media_kind == "video" and has_secondary:
        raise PlanInvalid("Video plans do not accept a secondary asset.")

    return CompiledPlan(
        operations=normalized,
        digest=digest_json(
            {
                "media_kind": media_kind,
                "operations": normalized,
            }
        ),
        operation_types=[item["type"] for item in normalized],
    )
