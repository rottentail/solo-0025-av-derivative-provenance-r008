"""Stable error types shared across the HTTP and application layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApplicationError(Exception):
    code: str
    message: str
    status: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}


class ValidationFailure(ApplicationError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "VALIDATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status=400,
            details=details or {},
        )


class UnsupportedMedia(ApplicationError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="UNSUPPORTED_MEDIA",
            message=message,
            status=415,
            details=details or {},
        )


class AssetNotFound(ApplicationError):
    def __init__(self, asset_id: str) -> None:
        super().__init__(
            code="ASSET_NOT_FOUND",
            message="The requested media asset does not exist.",
            status=404,
            details={"asset_id": asset_id},
        )


class RenditionNotFound(ApplicationError):
    def __init__(self, rendition_id: str) -> None:
        super().__init__(
            code="RENDITION_NOT_FOUND",
            message="The requested rendition does not exist.",
            status=404,
            details={"rendition_id": rendition_id},
        )


class RenditionNotCompleted(ApplicationError):
    def __init__(self, rendition_id: str, status: str) -> None:
        super().__init__(
            code="RENDITION_NOT_COMPLETED",
            message="Integrity checks require a completed rendition.",
            status=409,
            details={"rendition_id": rendition_id, "status": status},
        )


class PlanInvalid(ApplicationError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="PLAN_INVALID",
            message=message,
            status=422,
            details=details or {},
        )


class RenderFailed(ApplicationError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="RENDER_FAILED",
            message=message,
            status=409,
            details=details or {},
        )


class IntegrityMismatch(ApplicationError):
    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code="INTEGRITY_MISMATCH",
            message=message,
            status=409,
            details=details or {},
        )


class InvalidStateTransition(ApplicationError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            code="INVALID_STATE_TRANSITION",
            message="The requested lifecycle transition is not allowed.",
            status=409,
            details={"current": current, "target": target},
        )


class PersistenceFailure(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(
            code="PERSISTENCE_FAILURE",
            message=message,
            status=500,
        )


class InternalFailure(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="INTERNAL_ERROR",
            message="An unexpected internal error occurred.",
            status=500,
        )
