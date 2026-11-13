"""Application orchestration for ingestion, rendering, and audits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import (
    ApplicationError,
    IntegrityMismatch,
    PlanInvalid,
    RenditionNotCompleted,
)
from .models import (
    IntegrityAudit,
    MediaAsset,
    ProvenanceEdge,
    Rendition,
)
from .media import inspect_media
from .renditions import (
    compile_plan,
    execute_plan,
    reproduce_output,
)
from .serialization import (
    deep_copy,
    new_identifier,
    sha256_hex,
    utc_now,
)
from .storage import (
    ContentFileRepository,
    JsonStateFile,
    ProvenanceRepository,
)
from .validation import (
    require_object,
    require_string,
    validate_filename,
)


class MediaProvenanceApplication:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.content_files = ContentFileRepository(self.data_dir / "content")
        self.state_file = JsonStateFile(self.data_dir / "state.json")
        self.repository = ProvenanceRepository(self.state_file)
        # Legacy crash journals from older builds are never authoritative;
        # discard them so they cannot roll back or delete committed facts.
        self.content_recovery_path = self.data_dir / ".content.recovery.json"
        self.content_recovery_path.unlink(missing_ok=True)
        self._recover_interrupted_work()

    def _recover_interrupted_work(self) -> None:
        """Reconcile catalog relations, then garbage-collect orphan bytes.

        Ordering matters: state is healed first so that every content key
        still referenced by a surviving asset is known before any media
        byte is removed.  The whole routine is idempotent and therefore
        safe across repeated restarts.
        """
        self.repository.reconcile()
        referenced_keys = [
            str(raw["content_key"])
            for raw in self.state_file.read()["assets"].values()
            if isinstance(raw, dict) and raw.get("content_key")
        ]
        self.content_files.garbage_collect(referenced_keys)

    def _asset_bytes(self, asset: MediaAsset) -> bytes:
        return self.content_files.read(asset.content_key)

    def _rendition_view(self, rendition: Rendition) -> dict[str, Any]:
        value = rendition.to_dict()
        if rendition.output_asset_id:
            try:
                value["output_asset"] = self.repository.get_asset(
                    rendition.output_asset_id
                ).to_public_dict()
            except ApplicationError:
                value["output_asset"] = None
        else:
            value["output_asset"] = None
        return value

    def ingest_asset(self, filename: Any, data: bytes) -> dict[str, Any]:
        safe_name = validate_filename(filename)
        descriptor = inspect_media(data)
        digest = sha256_hex(data)
        content_key = self.content_files.key_for_digest(digest)
        existed = self.content_files.exists(content_key)
        self.content_files.write(data, digest)
        asset = MediaAsset(
            id=new_identifier("asset"),
            filename=safe_name,
            kind=descriptor.kind,
            sha256=digest,
            size_bytes=len(data),
            content_key=content_key,
            descriptor=descriptor,
            created_at=utc_now(),
        )
        try:
            persisted = self.repository.create_asset(asset)
        except Exception:
            if not existed:
                self.content_files.remove(content_key)
            raise
        return persisted.to_public_dict()

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        return self.repository.get_asset(asset_id).to_public_dict()

    def create_rendition(self, payload: Any) -> dict[str, Any]:
        request = require_object(payload, "request")
        primary_asset_id = require_string(
            request.get("primary_asset_id"),
            "primary_asset_id",
            maximum=96,
        )
        secondary_value = request.get("secondary_asset_id")
        secondary_asset_id = None
        if secondary_value is not None:
            secondary_asset_id = require_string(
                secondary_value,
                "secondary_asset_id",
                maximum=96,
            )
        output_name = validate_filename(request.get("output_name"))

        primary = self.repository.get_asset(primary_asset_id)
        secondary = (
            self.repository.get_asset(secondary_asset_id)
            if secondary_asset_id
            else None
        )
        if secondary is not None and secondary.kind != primary.kind:
            raise PlanInvalid("Primary and secondary assets must share one media kind.")

        plan = compile_plan(
            primary.kind,
            request.get("operations"),
            has_secondary=secondary is not None,
        )
        if secondary is not None and "audio_mix" not in plan.operation_types:
            raise PlanInvalid(
                "A secondary audio asset requires an audio_mix operation."
            )
        if primary.kind == "audio" and not output_name.lower().endswith(".wav"):
            raise PlanInvalid("Audio output_name must end with .wav.")
        if primary.kind == "video" and not output_name.lower().endswith(".y4m"):
            raise PlanInvalid("Video output_name must end with .y4m.")

        now = utc_now()
        rendition = Rendition(
            id=new_identifier("rendition"),
            primary_asset_id=primary.id,
            secondary_asset_id=secondary.id if secondary else None,
            operations=plan.operations,
            plan_digest=plan.digest,
            output_name=output_name,
            status="queued",
            transitions=[{"from": None, "to": "queued", "at": now}],
            output_asset_id=None,
            error_code=None,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_rendition(rendition)
        self.repository.transition_rendition(rendition.id, "running")

        try:
            primary_bytes = self._asset_bytes(primary)
            secondary_bytes = self._asset_bytes(secondary) if secondary else None
            outcome = execute_plan(
                primary_bytes,
                plan.operations,
                secondary_bytes,
            )
            output_digest = sha256_hex(outcome.data)
            output_key = self.content_files.key_for_digest(output_digest)
            output_existed = self.content_files.exists(output_key)
            self.content_files.write(outcome.data, output_digest)
            output_asset = MediaAsset(
                id=new_identifier("asset"),
                filename=output_name,
                kind=outcome.descriptor.kind,
                sha256=output_digest,
                size_bytes=len(outcome.data),
                content_key=output_key,
                descriptor=outcome.descriptor,
                created_at=utc_now(),
            )
            edge = ProvenanceEdge(
                id=new_identifier("edge"),
                rendition_id=rendition.id,
                parent_asset_ids=[
                    item
                    for item in (primary.id, secondary.id if secondary else None)
                    if item is not None
                ],
                operation_types=list(outcome.operation_types),
                plan_digest=plan.digest,
                output_asset_id=output_asset.id,
                created_at=utc_now(),
            )
            try:
                completed = self.repository.complete_rendition(
                    rendition.id,
                    output_asset,
                    edge,
                )
            except Exception:
                if not output_existed:
                    self.content_files.remove(output_key)
                raise
            return self._rendition_view(completed)
        except ApplicationError as exc:
            current = self.repository.get_rendition(rendition.id)
            if current.status == "running":
                self.repository.fail_rendition(
                    rendition.id,
                    exc.code,
                    exc.message,
                )
            raise
        except Exception as exc:
            current = self.repository.get_rendition(rendition.id)
            if current.status == "running":
                self.repository.fail_rendition(
                    rendition.id,
                    "RENDER_FAILED",
                    "The rendition failed before completion.",
                )
            raise ApplicationError(
                code="RENDER_FAILED",
                message="The rendition failed before completion.",
                status=409,
            ) from exc

    def get_rendition(self, rendition_id: str) -> dict[str, Any]:
        return self._rendition_view(self.repository.get_rendition(rendition_id))

    def get_lineage(self, rendition_id: str) -> dict[str, Any]:
        return self.repository.lineage_for(rendition_id)

    def _begin_integrity_audit(self, rendition_id: str) -> IntegrityAudit:
        now = utc_now()
        audit = IntegrityAudit(
            id=new_identifier("audit"),
            rendition_id=rendition_id,
            status="pending",
            expected_digest=None,
            reproduced_digest=None,
            expected_descriptor=None,
            reproduced_descriptor=None,
            reason=None,
            created_at=now,
            updated_at=now,
        )
        return self.repository.insert_integrity_audit(audit)

    def _finish_mismatch(
        self,
        audit: IntegrityAudit,
        reason: str,
        *,
        expected_digest: str | None = None,
        expected_descriptor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finished = self.repository.settle_integrity_audit(
            audit.id,
            status="mismatch",
            expected_digest=expected_digest,
            reproduced_digest=None,
            expected_descriptor=expected_descriptor,
            reproduced_descriptor=None,
            reason=reason,
        )
        body = finished.to_dict()
        raise IntegrityMismatch(
            "The recorded rendition did not reproduce exactly.",
            details={
                "audit": body,
                "reason": reason,
            },
        )

    def verify_rendition(self, rendition_id: str) -> dict[str, Any]:
        rendition = self.repository.get_rendition(rendition_id)
        if rendition.status != "completed" or not rendition.output_asset_id:
            raise RenditionNotCompleted(rendition_id, rendition.status)

        audit = self._begin_integrity_audit(rendition_id)
        try:
            edge = self.repository.get_edge(rendition_id)
            output_asset = self.repository.get_asset(rendition.output_asset_id)
            primary_asset = self.repository.get_asset(rendition.primary_asset_id)
            secondary_asset = (
                self.repository.get_asset(rendition.secondary_asset_id)
                if rendition.secondary_asset_id
                else None
            )
        except ApplicationError:
            self._finish_mismatch(audit, "provenance_unavailable")

        expected_descriptor = output_asset.descriptor.to_dict()
        try:
            recorded_output = self._asset_bytes(output_asset)
            primary_bytes = self._asset_bytes(primary_asset)
            secondary_bytes = (
                self._asset_bytes(secondary_asset) if secondary_asset else None
            )
        except ApplicationError:
            self._finish_mismatch(
                audit,
                "source_or_output_unavailable",
                expected_digest=output_asset.sha256,
                expected_descriptor=expected_descriptor,
            )

        if edge.plan_digest != rendition.plan_digest:
            self._finish_mismatch(
                audit,
                "plan_digest_drift",
                expected_digest=output_asset.sha256,
                expected_descriptor=expected_descriptor,
            )
        if edge.parent_asset_ids != [
            item
            for item in (
                rendition.primary_asset_id,
                rendition.secondary_asset_id,
            )
            if item is not None
        ]:
            self._finish_mismatch(
                audit,
                "parent_set_drift",
                expected_digest=output_asset.sha256,
                expected_descriptor=expected_descriptor,
            )

        outcome = reproduce_output(
            output_asset=output_asset,
            recorded_output_bytes=recorded_output,
            primary_bytes=primary_bytes,
            operations=deep_copy(rendition.operations),
            secondary_bytes=secondary_bytes,
        )
        finished = self.repository.settle_integrity_audit(
            audit.id,
            status=outcome.status,
            expected_digest=outcome.expected_digest,
            reproduced_digest=outcome.reproduced_digest,
            expected_descriptor=outcome.expected_descriptor,
            reproduced_descriptor=outcome.reproduced_descriptor,
            reason=outcome.reason,
        )
        if finished.status == "verified":
            return finished.to_dict()
        raise IntegrityMismatch(
            "The recorded rendition did not reproduce exactly.",
            details={
                "audit": finished.to_dict(),
                "reason": finished.reason,
            },
        )

    def close(self) -> None:
        return
