"""UID-based resolution and audited selection of DICOM-RT treatment chains."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


CHAIN_SCHEMA_VERSION = "ASCEND-DICOM-chain-v1"


def _opaque_id(payload: dict[str, Any]) -> str:
    """Create a stable, non-PHI identifier from the chain's UID relationships."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "chain_" + hashlib.sha256(encoded).hexdigest()[:20]


def _same_identity(records: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Reject candidate chains spanning conflicting patient or reference frames."""
    reasons: list[str] = []
    patients = {item.get("patient_id") for item in records if item and item.get("patient_id")}
    frames = {item.get("frame_of_reference_uid") for item in records if item and item.get("frame_of_reference_uid")}
    if len(patients) > 1:
        reasons.append("patient_identity_conflict")
    if len(frames) > 1:
        reasons.append("frame_of_reference_conflict")
    return not reasons, reasons


def _candidate(
    dose: dict[str, Any],
    plan: dict[str, Any] | None,
    struct: dict[str, Any] | None,
    series_uid: str | None,
    image_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one candidate while keeping validity independent from selection."""
    dose_uid = dose.get("sop_instance_uid") or None
    plan_uid = plan.get("sop_instance_uid") if plan else None
    struct_uid = struct.get("sop_instance_uid") if struct else None
    dose_plan_refs = set(dose.get("referenced_rtplan_uids", []))
    plan_struct_refs = set(plan.get("referenced_rtstruct_uids", [])) if plan else set()
    struct_series_refs = set(struct.get("referenced_image_series_uids", [])) if struct else set()
    unresolved: list[str] = []
    if not plan or plan_uid not in dose_plan_refs:
        unresolved.append("rtdose_to_rtplan_reference")
    if not struct or struct_uid not in plan_struct_refs:
        unresolved.append("rtplan_to_rtstruct_reference")
    if not series_uid or series_uid not in struct_series_refs or not image_records:
        unresolved.append("rtstruct_to_image_series_reference")
    identity_ok, identity_reasons = _same_identity([dose, plan or {}, struct or {}, *image_records])
    if identity_reasons:
        validity = "invalid"
    elif unresolved:
        validity = "override_eligible"
    else:
        validity = "complete"
    identity = {
        "rtdose_uid": dose_uid,
        "rtplan_uid": plan_uid,
        "rtstruct_uid": struct_uid,
        "image_series_uid": series_uid,
    }
    return {
        "schema_version": CHAIN_SCHEMA_VERSION,
        "chain_id": _opaque_id(identity),
        "validity_status": validity,
        "selection_status": "unselected",
        "validity_reasons": identity_reasons,
        "unresolved_references": unresolved,
        "identity_consistent": identity_ok,
        "objects": {
            "rtdose": dose.get("path"),
            "rtplan": plan.get("path") if plan else None,
            "rtstruct": struct.get("path") if struct else None,
            "image_series": [item["path"] for item in image_records],
        },
        "uids": identity,
        "display": {
            "plan_label": plan.get("plan_label", "") if plan else "",
            "dose_summation_type": dose.get("dose_summation_type", ""),
            "image_count": len(image_records),
        },
    }


def resolve_dicom_chains(objects: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Resolve candidate RT chains without silently choosing among alternatives.

    UID references are authoritative.  Same-frame fallbacks only make a chain
    eligible for a recorded override; they do not make the linkage complete.
    A single complete chain is selected automatically, while multiple complete
    chains remain blocked until an explicit chain identifier is supplied.
    """
    doses = objects.get("RTDOSE", [])
    plans = objects.get("RTPLAN", [])
    structs = objects.get("RTSTRUCT", [])
    images_by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for modality in ("CT", "MR", "PT"):
        for item in objects.get(modality, []):
            if item.get("series_instance_uid"):
                images_by_series[item["series_instance_uid"]].append(item)
    plans_by_uid = {item.get("sop_instance_uid"): item for item in plans if item.get("sop_instance_uid")}
    structs_by_uid = {item.get("sop_instance_uid"): item for item in structs if item.get("sop_instance_uid")}
    candidates: dict[str, dict[str, Any]] = {}
    for dose in doses:
        referenced_plans = [plans_by_uid[uid] for uid in dose.get("referenced_rtplan_uids", []) if uid in plans_by_uid]
        plan_options: list[dict[str, Any] | None] = referenced_plans or plans or [None]
        for plan in plan_options:
            referenced_structs = [
                structs_by_uid[uid] for uid in (plan.get("referenced_rtstruct_uids", []) if plan else [])
                if uid in structs_by_uid
            ]
            struct_options: list[dict[str, Any] | None] = referenced_structs or structs or [None]
            for struct in struct_options:
                referenced_series = list(struct.get("referenced_image_series_uids", [])) if struct else []
                series_options = [uid for uid in referenced_series if uid in images_by_series]
                if not series_options:
                    same_frame = [
                        uid for uid, records in images_by_series.items()
                        if not struct.get("frame_of_reference_uid")
                        or any(record.get("frame_of_reference_uid") == struct.get("frame_of_reference_uid") for record in records)
                    ] if struct else list(images_by_series)
                    series_options = same_frame or [None]
                for series_uid in series_options:
                    candidate = _candidate(dose, plan, struct, series_uid, images_by_series.get(series_uid, []))
                    candidates[candidate["chain_id"]] = candidate
    ordered = sorted(candidates.values(), key=lambda item: (
        {"complete": 0, "override_eligible": 1, "invalid": 2}[item["validity_status"]],
        item["chain_id"],
    ))
    # Selection is a workflow decision, not evidence of DICOM validity.  Keep
    # these state dimensions separate in every candidate record.
    complete = [item for item in ordered if item["validity_status"] == "complete"]
    if len(complete) == 1:
        complete[0]["selection_status"] = "selected"
    elif len(complete) > 1:
        for item in complete:
            item["selection_status"] = "selection_required"
    elif ordered:
        for item in ordered:
            if item["validity_status"] == "override_eligible":
                item["selection_status"] = "selection_required"
    return ordered


def select_chain(
    chains: list[dict[str, Any]],
    chain_id: str,
    allow_incomplete: bool = False,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Select a chain and permanently record any incomplete-linkage override."""
    selected = next((item for item in chains if item.get("chain_id") == chain_id), None)
    if selected is None:
        raise ValueError(f"Unknown DICOM chain ID: {chain_id}")
    if selected["validity_status"] == "invalid":
        raise ValueError("The selected DICOM chain has conflicting patient or Frame of Reference identity.")
    if selected["validity_status"] == "override_eligible":
        if not allow_incomplete:
            raise ValueError("The selected DICOM chain requires an explicit incomplete-reference override.")
        if not str(override_reason or "").strip():
            raise ValueError("An incomplete-reference override requires a non-empty reason.")
    for item in chains:
        item["selection_status"] = "selected" if item is selected else "unselected"
    return {
        "chain_id": chain_id,
        "selection_method": "explicit_override" if selected["validity_status"] == "override_eligible" else "explicit_or_unique_complete",
        "override_confirmed": selected["validity_status"] == "override_eligible",
        "override_reason": str(override_reason).strip() if override_reason else None,
        "unresolved_references": list(selected.get("unresolved_references", [])),
    }
