"""Identity-first matching of Eclipse reference structures to ASCEND results."""

from __future__ import annotations

import re
from typing import Any

from ascend.models.case import ASCENDCase

from .schemas import MatchResult, ReferenceRecord


def _norm(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def structure_candidates(case: ASCENDCase) -> list[dict[str, Any]]:
    """Handle structure candidates for the enclosing ASCEND workflow."""
    result = case.layer1.result or {}
    manifest = result.get("manifest", {})
    roles: dict[str, str] = {}
    for role, canonical in manifest.get("effective_structure_roles", {}).items():
        values = canonical if isinstance(canonical, list) else [canonical]
        for value in values:
            roles[str(value)] = str(role)
    candidates: list[dict[str, Any]] = []
    for item in manifest.get("roi_inventory", []):
        identity = item.get("roi_identity") or {
            "rtstruct_sop_instance_uid": manifest.get("rtstruct_uid"),
            "roi_number": item.get("roi_number"),
        }
        canonical = item.get("canonical_mapping")
        candidates.append({
            "structure_identity": {
                "rtstruct_sop_instance_uid": identity.get("rtstruct_sop_instance_uid"),
                "roi_number": int(identity["roi_number"]) if identity.get("roi_number") is not None else None,
            },
            "roi_number": int(item["roi_number"]) if item.get("roi_number") is not None else None,
            "roi_name": item.get("original_name"),
            "canonical_structure": canonical,
            "structure_role": roles.get(str(canonical)),
            "mapping_status": item.get("mapping_status"),
            "rasterisation_status": item.get("rasterisation_status"),
        })
    return candidates


def _identity_conflict(reference: ReferenceRecord, manifest: dict[str, Any]) -> str | None:
    expected = {
        "case_id": manifest.get("case_id"),
        "rtstruct_uid": manifest.get("rtstruct_uid"),
        "rtdose_uid": manifest.get("rtdose_uid"),
        "rtplan_uid": manifest.get("rtplan_uid"),
    }
    supplied = {
        "case_id": reference.case_id,
        "rtstruct_uid": reference.rtstruct_uid,
        "rtdose_uid": reference.rtdose_uid,
        "rtplan_uid": reference.rtplan_uid,
    }
    for field in ("case_id", "rtstruct_uid", "rtdose_uid", "rtplan_uid"):
        if supplied[field] and expected[field] and _norm(supplied[field]) != _norm(expected[field]):
            return f"Reference {field} {supplied[field]!r} conflicts with selected ASCEND {field} {expected[field]!r}."
    return None


def match_reference(case: ASCENDCase, reference: ReferenceRecord) -> MatchResult:
    """Handle match reference for the enclosing ASCEND workflow."""
    result = case.layer1.result or {}
    manifest = result.get("manifest", {})
    conflict = _identity_conflict(reference, manifest)
    if conflict:
        return MatchResult("identity_conflict", reason=conflict)
    candidates = structure_candidates(case)
    if reference.rtstruct_uid and reference.roi_number is not None:
        exact = [
            item for item in candidates
            if item["structure_identity"]["rtstruct_sop_instance_uid"] == reference.rtstruct_uid
            and item["roi_number"] == reference.roi_number
        ]
        if len(exact) == 1:
            warnings = []
            if _norm(exact[0]["roi_name"]) != _norm(reference.roi_name):
                warnings.append("reference_roi_name_differs_from_identity_bound_ascend_name")
            return MatchResult("matched_exact_identity", exact[0], warnings=warnings)
        if len(exact) > 1:
            return MatchResult("ambiguous", reason="Multiple Layer 1 ROI inventory rows share the supplied identity.")
        return MatchResult("not_found", reason="The supplied RTSTRUCT UID and ROI number are not present in the Layer 1 ROI inventory.")

    number_matches = [item for item in candidates if reference.roi_number is not None and item["roi_number"] == reference.roi_number]
    role_matches = [
        item for item in candidates
        if reference.structure_role and _norm(item.get("structure_role")) == _norm(reference.structure_role)
    ]
    name_matches = [
        item for item in candidates
        if _norm(reference.roi_name) in {_norm(item.get("roi_name")), _norm(item.get("canonical_structure"))}
    ]
    unique_sources = [matches[0] for matches in (number_matches, role_matches, name_matches) if len(matches) == 1]
    unique_identities = {
        (item["structure_identity"]["rtstruct_sop_instance_uid"], item["roi_number"])
        for item in unique_sources
    }
    if len(unique_identities) > 1:
        return MatchResult(
            "identity_conflict",
            reason="ROI number, recorded role mapping, and/or name fallback resolve to different ASCEND structures.",
        )
    for matches, method in (
        (number_matches, "roi_number_without_reference_rtstruct_uid"),
        (role_matches, "validated_structure_role_mapping"),
        (name_matches, "unique_name_only_fallback"),
    ):
        if len(matches) == 1:
            return MatchResult("matched_unique_fallback", matches[0], warnings=[method])
        if len(matches) > 1:
            return MatchResult("ambiguous", reason=f"{method} matched multiple RTSTRUCT ROI inventory records.")
    return MatchResult("not_found", reason="No unique validated ROI mapping or unique name match was found.")
