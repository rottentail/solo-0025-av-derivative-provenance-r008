"""Durable domain records and their lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidStateTransition
from .serialization import deep_copy, utc_now


TERMINAL_RENDITION_STATES = {"completed", "failed"}
TERMINAL_CHECK_STATES = {"verified", "mismatch"}


@dataclass(frozen=True)
class MediaDescriptor:
    kind: str
    mime_type: str
    container: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "mime_type": self.mime_type,
            "container": self.container,
            "properties": deep_copy(self.properties),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MediaDescriptor":
        return cls(
            kind=str(value["kind"]),
            mime_type=str(value["mime_type"]),
            container=str(value["container"]),
            properties=deep_copy(dict(value.get("properties") or {})),
        )


@dataclass(frozen=True)
class MediaAsset:
    id: str
    filename: str
    kind: str
    sha256: str
    size_bytes: int
    content_key: str
    descriptor: MediaDescriptor
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_key": self.content_key,
            "descriptor": self.descriptor.to_dict(),
            "created_at": self.created_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("content_key", None)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MediaAsset":
        return cls(
            id=str(value["id"]),
            filename=str(value["filename"]),
            kind=str(value["kind"]),
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            content_key=str(value["content_key"]),
            descriptor=MediaDescriptor.from_dict(dict(value["descriptor"])),
            created_at=str(value["created_at"]),
        )


@dataclass
class Rendition:
    id: str
    primary_asset_id: str
    secondary_asset_id: str | None
    operations: list[dict[str, Any]]
    plan_digest: str
    output_name: str
    status: str
    transitions: list[dict[str, str | None]]
    output_asset_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "primary_asset_id": self.primary_asset_id,
            "secondary_asset_id": self.secondary_asset_id,
            "operations": deep_copy(self.operations),
            "plan_digest": self.plan_digest,
            "output_name": self.output_name,
            "status": self.status,
            "transitions": deep_copy(self.transitions),
            "output_asset_id": self.output_asset_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Rendition":
        return cls(
            id=str(value["id"]),
            primary_asset_id=str(value["primary_asset_id"]),
            secondary_asset_id=(
                str(value["secondary_asset_id"])
                if value.get("secondary_asset_id")
                else None
            ),
            operations=deep_copy(list(value.get("operations") or [])),
            plan_digest=str(value["plan_digest"]),
            output_name=str(value["output_name"]),
            status=str(value["status"]),
            transitions=deep_copy(list(value.get("transitions") or [])),
            output_asset_id=(
                str(value["output_asset_id"]) if value.get("output_asset_id") else None
            ),
            error_code=(
                str(value["error_code"]) if value.get("error_code") is not None else None
            ),
            error_message=(
                str(value["error_message"])
                if value.get("error_message") is not None
                else None
            ),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )

    def transition_to(self, target: str) -> None:
        allowed = {
            "queued": {"running"},
            "running": {"completed", "failed"},
            "completed": set(),
            "failed": set(),
        }
        if target not in allowed.get(self.status, set()):
            raise InvalidStateTransition(self.status, target)
        previous = self.status
        now = utc_now()
        self.status = target
        self.updated_at = now
        self.transitions.append(
            {"from": previous, "to": target, "at": now}
        )


@dataclass(frozen=True)
class ProvenanceEdge:
    id: str
    rendition_id: str
    parent_asset_ids: list[str]
    operation_types: list[str]
    plan_digest: str
    output_asset_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rendition_id": self.rendition_id,
            "parent_asset_ids": list(self.parent_asset_ids),
            "operation_types": list(self.operation_types),
            "plan_digest": self.plan_digest,
            "output_asset_id": self.output_asset_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProvenanceEdge":
        return cls(
            id=str(value["id"]),
            rendition_id=str(value["rendition_id"]),
            parent_asset_ids=[str(item) for item in value["parent_asset_ids"]],
            operation_types=[str(item) for item in value.get("operation_types", [])],
            plan_digest=str(value["plan_digest"]),
            output_asset_id=str(value["output_asset_id"]),
            created_at=str(value["created_at"]),
        )


@dataclass
class IntegrityAudit:
    id: str
    rendition_id: str
    status: str
    expected_digest: str | None
    reproduced_digest: str | None
    expected_descriptor: dict[str, Any] | None
    reproduced_descriptor: dict[str, Any] | None
    reason: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rendition_id": self.rendition_id,
            "status": self.status,
            "expected_digest": self.expected_digest,
            "reproduced_digest": self.reproduced_digest,
            "expected_descriptor": deep_copy(self.expected_descriptor),
            "reproduced_descriptor": deep_copy(self.reproduced_descriptor),
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IntegrityAudit":
        return cls(
            id=str(value["id"]),
            rendition_id=str(value["rendition_id"]),
            status=str(value["status"]),
            expected_digest=(
                str(value["expected_digest"])
                if value.get("expected_digest") is not None
                else None
            ),
            reproduced_digest=(
                str(value["reproduced_digest"])
                if value.get("reproduced_digest") is not None
                else None
            ),
            expected_descriptor=deep_copy(value.get("expected_descriptor")),
            reproduced_descriptor=deep_copy(value.get("reproduced_descriptor")),
            reason=(
                str(value["reason"]) if value.get("reason") is not None else None
            ),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )

    def finish(
        self,
        *,
        status: str,
        expected_digest: str | None,
        reproduced_digest: str | None,
        expected_descriptor: dict[str, Any] | None,
        reproduced_descriptor: dict[str, Any] | None,
        reason: str | None,
    ) -> None:
        if self.status in TERMINAL_CHECK_STATES:
            raise InvalidStateTransition(self.status, status)
        if self.status != "pending" or status not in TERMINAL_CHECK_STATES:
            raise InvalidStateTransition(self.status, status)
        self.status = status
        self.expected_digest = expected_digest
        self.reproduced_digest = reproduced_digest
        self.expected_descriptor = deep_copy(expected_descriptor)
        self.reproduced_descriptor = deep_copy(reproduced_descriptor)
        self.reason = reason
        self.updated_at = utc_now()
