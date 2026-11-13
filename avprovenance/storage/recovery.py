"""Startup reconciliation for interrupted and partially persisted state.

The reconciler establishes a single consistent durable fact after any crash.
It runs against the committed JSON state and is deliberately idempotent:
repeated restarts converge to the same result.

Rules
-----

* Renditions interrupted in ``queued``/``running`` are forced to a terminal
  ``failed`` state with a stable ``RECOVERY_INTERRUPTED`` code so an
  unfinished task can never be mistaken for a delivered asset.
* A rendition is exposed as ``completed`` only when the whole relation is
  closed: the output asset record exists and exactly one provenance edge
  links this rendition to that asset, matching the recorded parents and
  plan digest.  Anything short of closure is demoted to ``failed`` instead
  of exposing a half-written product.
* Already-terminal records are never rewritten, so delivered sounds,
  frames, and lineage cannot be overwritten by older residue.
* Provenance edges and asset records that no longer participate in a closed
  relation are dropped from the catalog; content garbage collection then
  removes only the bytes that no surviving asset references.
* Malformed records are skipped rather than preventing the service from
  starting.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..serialization import utc_now
from .state_file import DEFAULT_STATE


INTERRUPTED_ERROR_CODE = "RECOVERY_INTERRUPTED"
INTERRUPTED_ERROR_MESSAGE = (
    "The rendition was interrupted before its result was durably completed."
)


def _is_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_valid_asset(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    string_fields = (
        "id",
        "filename",
        "kind",
        "sha256",
        "content_key",
        "created_at",
    )
    if not all(_is_string(raw.get(key)) for key in string_fields):
        return False
    if not isinstance(raw.get("size_bytes"), int):
        return False
    descriptor = raw.get("descriptor")
    if not isinstance(descriptor, dict):
        return False
    return all(
        _is_string(descriptor.get(key))
        for key in ("kind", "mime_type", "container")
    )


def _is_valid_edge(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if not all(
        _is_string(raw.get(key))
        for key in ("id", "rendition_id", "plan_digest", "output_asset_id")
    ):
        return False
    parents = raw.get("parent_asset_ids")
    if not isinstance(parents, list) or not all(
        _is_string(item) for item in parents
    ):
        return False
    operation_types = raw.get("operation_types")
    if not isinstance(operation_types, list) or not all(
        _is_string(item) for item in operation_types
    ):
        return False
    return _is_string(raw.get("created_at"))


def _is_valid_rendition(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    required = (
        "id",
        "primary_asset_id",
        "plan_digest",
        "output_name",
        "status",
        "created_at",
        "updated_at",
    )
    return all(_is_string(raw.get(key)) for key in required) and isinstance(
        raw.get("transitions"), list
    )


def _expected_parents(raw: dict[str, Any]) -> list[str]:
    parents = [str(raw["primary_asset_id"])]
    secondary = raw.get("secondary_asset_id")
    if secondary:
        parents.append(str(secondary))
    return parents


def _force_failure(raw: dict[str, Any]) -> dict[str, Any]:
    """Move a non-terminal rendition to failed without resurrecting history."""
    previous = raw.get("status")
    raw["status"] = "failed"
    raw["output_asset_id"] = None
    raw["error_code"] = INTERRUPTED_ERROR_CODE
    raw["error_message"] = INTERRUPTED_ERROR_MESSAGE
    raw["updated_at"] = utc_now()
    transitions = raw.setdefault("transitions", [])
    transitions.append(
        {"from": previous, "to": "failed", "at": raw["updated_at"]}
    )
    return raw


def reconcile_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a relation-closed copy of ``state`` and a summary of repairs."""
    if not isinstance(state, dict):
        state = deepcopy(DEFAULT_STATE)

    repaired = {
        "dropped_assets": 0,
        "dropped_renditions": 0,
        "dropped_edges": 0,
        "interrupted_renditions": 0,
        "demoted_renditions": 0,
    }

    # --- assets: keep only well-formed immutable records ----------------
    assets = state.get("assets")
    if not isinstance(assets, dict):
        assets = {}
    valid_assets: dict[str, dict[str, Any]] = {}
    for asset_id, raw in assets.items():
        if _is_valid_asset(raw) and raw.get("id") == asset_id:
            valid_assets[asset_id] = raw
        else:
            repaired["dropped_assets"] += 1

    # --- renditions: classify each into terminal-closed or failed -------
    renditions = state.get("renditions")
    if not isinstance(renditions, dict):
        renditions = {}
    valid_renditions: dict[str, dict[str, Any]] = {}

    edges = state.get("provenance_edges")
    if not isinstance(edges, list):
        edges = []
    valid_edges: list[dict[str, Any]] = []

    for rendition_id, raw in renditions.items():
        if not _is_valid_rendition(raw) or raw.get("id") != rendition_id:
            repaired["dropped_renditions"] += 1
            continue

        status = raw["status"]
        if status not in {"queued", "running", "completed", "failed"}:
            # Unknown lifecycle state cannot be trusted: fail it closed.
            raw = _force_failure(raw)
            repaired["interrupted_renditions"] += 1
            valid_renditions[rendition_id] = raw
            continue

        if status in ("queued", "running"):
            raw = _force_failure(raw)
            repaired["interrupted_renditions"] += 1
            valid_renditions[rendition_id] = raw
            continue

        if status == "failed":
            raw["output_asset_id"] = None
            valid_renditions[rendition_id] = raw
            continue

        # Completed renditions must prove a fully closed relation.
        output_id = raw.get("output_asset_id")
        matching_edges = [
            edge
            for edge in edges
            if _is_valid_edge(edge)
            and edge.get("rendition_id") == rendition_id
            and edge.get("output_asset_id") == output_id
        ]
        relation_closed = (
            _is_string(output_id)
            and output_id in valid_assets
            and len(matching_edges) == 1
            and matching_edges[0]["parent_asset_ids"]
            == _expected_parents(raw)
            and matching_edges[0]["plan_digest"] == raw["plan_digest"]
        )
        if not relation_closed:
            raw = _force_failure(raw)
            repaired["demoted_renditions"] += 1
            valid_renditions[rendition_id] = raw
            continue

        valid_renditions[rendition_id] = raw
        edge = matching_edges[0]
        valid_edges.append(edge)

    repaired["dropped_edges"] = len(edges) - len(valid_edges)

    # --- integrity checks: keep well-formed, already-immutable records --
    audits = state.get("integrity_checks")
    if not isinstance(audits, dict):
        audits = {}
    valid_audits: dict[str, Any] = {}
    for audit_id, raw in audits.items():
        if isinstance(raw, dict) and raw.get("id") == audit_id:
            valid_audits[audit_id] = raw

    reconciled: dict[str, Any] = {
        "schema_version": state.get("schema_version", DEFAULT_STATE["schema_version"]),
        "assets": valid_assets,
        "renditions": valid_renditions,
        "provenance_edges": valid_edges,
        "integrity_checks": valid_audits,
    }
    reconciled["_recovery"] = repaired
    return reconciled
