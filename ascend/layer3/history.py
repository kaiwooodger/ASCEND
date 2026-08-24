"""Validated fraction-event reconstruction shared by Layer 3.1A, 3.1B and 3.1C.

The engine converts explicit per-fraction sources or a declared repeated-
identical component total into biological fraction events. Integrated
components are summed physically within each fraction before any nonlinear
model is evaluated. Sequential components remain separate events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np

from ascend.scientific.legacy import layer21_validated as handoff
from ascend.treatment.models import TreatmentContext
from ascend.validation.provenance import canonical_hash, file_hash


FRACTION_HISTORY_SCHEMA_VERSION = "ASCEND-L3.1-fraction-history-v1"
FRACTION_HISTORY_ALGORITHM_VERSION = "ASCEND-L3.1-fraction-events-v1.0"


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: str
    reason_code: str | None = None
    explanation: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id, "status": self.status,
            "reason_code": self.reason_code, "explanation": self.explanation,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class FractionEvent:
    event_id: str
    temporal_order: int
    biological_fraction_index: int
    physical_components: tuple[str, ...]
    combined_fraction_dose_field: np.ndarray = field(repr=False, compare=False)
    source_plan_identifiers: tuple[str, ...] = ()
    source_dose_identifiers: tuple[str, ...] = ()
    geometry_reference: str = ""
    registration_reference: str = "same_validated_physical_geometry"
    delivery_time: float | None = None
    delivery_time_unit: str | None = None
    repeated_fraction_information: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        dose = np.asarray(self.combined_fraction_dose_field, dtype=np.float64)
        return {
            "event_id": self.event_id,
            "temporal_order": self.temporal_order,
            "biological_fraction_index": self.biological_fraction_index,
            "physical_components": list(self.physical_components),
            "source_plan_identifiers": list(self.source_plan_identifiers),
            "source_dose_identifiers": list(self.source_dose_identifiers),
            "geometry_reference": self.geometry_reference,
            "registration_reference": self.registration_reference,
            "delivery_time": self.delivery_time,
            "delivery_time_unit": self.delivery_time_unit,
            "repeated_fraction_information": dict(self.repeated_fraction_information),
            "dose_field": {
                "shape": list(dose.shape), "dtype": "float64",
                "minimum_gy": float(np.min(dose)), "maximum_gy": float(np.max(dose)),
                "mean_gy": float(np.mean(dose)),
                "sha256": hashlib.sha256(np.ascontiguousarray(dose).view(np.uint8)).hexdigest(),
            },
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class FractionHistory:
    treatment_approach: str
    events: tuple[FractionEvent, ...]
    geometry_reference: str
    registration_state: str
    component_grouping: str
    gate_results: tuple[GateResult, ...]
    warnings: tuple[str, ...]
    provenance: dict[str, Any]

    @property
    def history_hash(self) -> str:
        return canonical_hash(self.metadata(include_hash=False))

    @property
    def maximum_fraction_dose_field(self) -> np.ndarray:
        return np.maximum.reduce([
            np.asarray(event.combined_fraction_dose_field, dtype=np.float32)
            for event in self.events
        ])

    def metadata(self, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": FRACTION_HISTORY_SCHEMA_VERSION,
            "algorithm_version": FRACTION_HISTORY_ALGORITHM_VERSION,
            "treatment_approach": self.treatment_approach,
            "number_of_biological_fraction_events": len(self.events),
            "geometry_reference": self.geometry_reference,
            "registration_state": self.registration_state,
            "component_grouping": self.component_grouping,
            "events": [event.metadata() for event in self.events],
            "gate_results": [gate.to_dict() for gate in self.gate_results],
            "warnings": list(self.warnings),
            "provenance": dict(self.provenance),
        }
        if include_hash:
            result["fraction_history_hash"] = self.history_hash
        return result


@dataclass(frozen=True)
class FractionHistoryBuild:
    status: str
    reason: str | None
    gate_results: tuple[GateResult, ...]
    history: FractionHistory | None


def _result_directory(value: Any) -> Path:
    source = Path(str(value or ""))
    return source.parent if source.name == "layer1_result.json" else source


def _geometry_payload(manifest: dict[str, Any], shape: tuple[int, ...]) -> dict[str, Any]:
    geometry = manifest.get("validated_geometry") or {}
    spacing = manifest.get("dose_grid", {}).get("voxel_spacing_mm")
    required = ("origin", "normal", "offsets", "spacing", "shape")
    if not isinstance(spacing, list) or len(spacing) != 3 or any(key not in geometry for key in required):
        raise ValueError("BIOLOGICAL_UPSTREAM_GEOMETRY_INVALID")
    if tuple(map(int, geometry["shape"])) != tuple(shape):
        raise ValueError("BIOLOGICAL_UPSTREAM_GEOMETRY_INVALID")
    return {
        "origin": geometry["origin"], "row_dir": geometry.get("row_dir", geometry.get("row_direction")),
        "col_dir": geometry.get("col_dir", geometry.get("column_direction")),
        "normal": geometry["normal"], "offsets": geometry["offsets"],
        "spacing": geometry["spacing"], "spacing_zyx_mm": spacing,
        "shape": list(shape), "frame_of_reference_uid": manifest.get("frame_of_reference_uid"),
    }


def _load_source(value: Any) -> dict[str, Any]:
    directory = _result_directory(value)
    result_path = directory / "layer1_result.json"
    if not result_path.is_file():
        raise ValueError("BIOLOGICAL_COMPONENT_DOSE_UNAVAILABLE")
    result, dose, _masks = handoff.load_handoff(directory)
    manifest = result.get("manifest", {})
    geometry = _geometry_payload(manifest, dose.shape)
    if not np.isfinite(dose).all() or np.any(dose < 0):
        raise ValueError("BIOLOGICAL_COMPONENT_DOSE_INVALID")
    dose_uid = str(manifest.get("rtdose_uid") or "")
    plan_uid = str(manifest.get("rtplan_uid") or "")
    if not dose_uid or not plan_uid:
        raise ValueError("BIOLOGICAL_SOURCE_IDENTIFIERS_UNRESOLVED")
    return {
        "dose": np.asarray(dose, dtype=np.float64), "manifest": manifest,
        "geometry": geometry, "geometry_hash": canonical_hash(geometry),
        "dose_uid": dose_uid, "plan_uid": plan_uid,
        "result_path": str(result_path), "result_hash": file_hash(result_path),
    }


def _positive_fraction_count(value: Any) -> int:
    try:
        numeric = float(value); result = int(numeric)
    except (TypeError, ValueError) as exc:
        raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED") from exc
    if not math.isfinite(numeric) or result <= 0 or numeric != result:
        raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
    return result


def reconstruct_fraction_history(
    configured_components: list[dict[str, Any]],
    treatment_context: TreatmentContext,
) -> FractionHistoryBuild:
    """Reconstruct biologically distinct fraction events from validated sources."""
    gates: list[GateResult] = []
    if not configured_components:
        gate = GateResult("GATE_1_FRACTION_HISTORY", "BLOCKED", "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
        return FractionHistoryBuild("BLOCKED", gate.reason_code, (gate,), None)
    try:
        prepared: list[dict[str, Any]] = []
        geometry_hashes: set[str] = set()
        for component_order, component in enumerate(configured_components):
            component_id = str(component.get("component_id") or "").strip()
            if not component_id:
                raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
            fraction_paths = list(component.get("fraction_layer1_result_paths") or [])
            method = str(component.get("fraction_dose_model") or (
                "explicit_fraction_doses" if fraction_paths else "identical_fractions"
            ))
            if method == "explicit_fraction_doses":
                if not fraction_paths:
                    raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
                sources = [_load_source(path) for path in fraction_paths]
                count = len(sources)
                if component.get("fraction_count") is not None and _positive_fraction_count(component["fraction_count"]) != count:
                    raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
                fractions = [source["dose"] for source in sources]
                repeated = False
            elif method == "identical_fractions":
                source = _load_source(component.get("layer1_result_path"))
                sources = [source]
                count = _positive_fraction_count(component.get("fraction_count"))
                fractions = [source["dose"] / float(count) for _ in range(count)]
                repeated = count > 1
            else:
                raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
            geometry_hashes.update(source["geometry_hash"] for source in sources)
            prepared.append({
                "component": component, "component_id": component_id, "component_order": component_order,
                "fractions": fractions, "sources": sources, "fraction_count": count,
                "repeated": repeated, "method": method,
            })
        gates.append(GateResult(
            "GATE_0_UPSTREAM_DATA", "PASS", evidence={
                "validated_component_count": len(prepared),
                "source_dose_uids": sorted({source["dose_uid"] for item in prepared for source in item["sources"]}),
            },
        ))
        if len(geometry_hashes) != 1:
            gate = GateResult(
                "GATE_2_SPATIAL_REGISTRATION", "BLOCKED", "BIOLOGICAL_SPATIAL_ACCUMULATION_UNRESOLVED",
                "Contributing dose components do not share one validated physical geometry.",
                {"geometry_hashes": sorted(geometry_hashes)},
            )
            return FractionHistoryBuild("BLOCKED", gate.reason_code, tuple([*gates, gate]), None)
        geometry_hash = next(iter(geometry_hashes))
        gates.append(GateResult(
            "GATE_2_SPATIAL_REGISTRATION", "PASS", evidence={
                "geometry_hash": geometry_hash, "registration": "identity_on_same_validated_geometry",
                "implicit_registration": False, "implicit_resampling": False,
            },
        ))

        approach = treatment_context.treatment_approach
        events: list[FractionEvent] = []
        if approach == "LRT_INTEGRATED" and len(prepared) > 1:
            counts = {item["fraction_count"] for item in prepared}
            if len(counts) != 1:
                raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
            count = next(iter(counts))
            for fraction_index in range(count):
                combined = np.add.reduce([item["fractions"][fraction_index] for item in prepared])
                events.append(_event(events, fraction_index + 1, prepared, combined, integrated=True))
            grouping = "same_fraction_physical_sum_before_biological_transformation"
        else:
            if approach == "UNKNOWN" and len(prepared) > 1:
                raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
            biological_index = 0
            for item in prepared:
                for fraction_index, dose in enumerate(item["fractions"], 1):
                    biological_index += 1
                    events.append(_event(events, biological_index, [item], dose, integrated=False, local_index=fraction_index))
            grouping = "biologically_separate_fraction_events"
        if not events:
            raise ValueError("BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED")
        gates.append(GateResult(
            "GATE_1_FRACTION_HISTORY", "PASS", evidence={
                "event_count": len(events), "component_grouping": grouping,
                "same_fraction_components_summed_first": grouping.startswith("same_fraction"),
            },
        ))
        history = FractionHistory(
            approach, tuple(events), geometry_hash, "PASS", grouping, tuple(gates), (),
            {
                "algorithm_version": FRACTION_HISTORY_ALGORITHM_VERSION,
                "treatment_context_hash": treatment_context.context_hash,
                "implicit_registration": False, "implicit_dose_warping": False,
                "cumulative_dose_schedule_inference": False,
            },
        )
        return FractionHistoryBuild("PASS", None, tuple(gates), history)
    except ValueError as exc:
        reason = str(exc) or "BIOLOGICAL_FRACTION_HISTORY_UNRESOLVED"
        gate_id = "GATE_2_SPATIAL_REGISTRATION" if reason == "BIOLOGICAL_SPATIAL_ACCUMULATION_UNRESOLVED" else (
            "GATE_0_UPSTREAM_DATA" if reason.startswith("BIOLOGICAL_UPSTREAM") or "SOURCE_IDENTIFIERS" in reason else
            "GATE_1_FRACTION_HISTORY"
        )
        gate = GateResult(gate_id, "BLOCKED", reason)
        return FractionHistoryBuild("BLOCKED", reason, tuple([*gates, gate]), None)


def _event(
    existing: list[FractionEvent],
    biological_index: int,
    prepared: list[dict[str, Any]],
    dose: np.ndarray,
    *,
    integrated: bool,
    local_index: int | None = None,
) -> FractionEvent:
    components = tuple(item["component_id"] for item in prepared)
    sources = [source for item in prepared for source in item["sources"]]
    configured_times = [item["component"].get("delivery_time") for item in prepared if item["component"].get("delivery_time") is not None]
    configured_units = [item["component"].get("delivery_time_unit") for item in prepared if item["component"].get("delivery_time_unit")]
    if configured_times and (
        len(configured_times) != len(prepared)
        or len(configured_units) != len(prepared)
        or len(set(map(float, configured_times))) != 1
        or len(set(map(str, configured_units))) != 1
    ):
        raise ValueError("BIOLOGICAL_DELIVERY_TIME_CONFLICT")
    delivery_time = float(configured_times[0]) if configured_times and len(set(map(float, configured_times))) == 1 else None
    delivery_unit = str(configured_units[0]) if configured_units and len(set(map(str, configured_units))) == 1 else None
    event_id = f"F{biological_index:03d}_" + "_".join(components)
    return FractionEvent(
        event_id=event_id, temporal_order=len(existing) + 1,
        biological_fraction_index=biological_index,
        physical_components=components,
        combined_fraction_dose_field=np.ascontiguousarray(dose, dtype=np.float64),
        source_plan_identifiers=tuple(dict.fromkeys(source["plan_uid"] for source in sources)),
        source_dose_identifiers=tuple(dict.fromkeys(source["dose_uid"] for source in sources)),
        geometry_reference=next(iter({source["geometry_hash"] for source in sources})),
        delivery_time=delivery_time, delivery_time_unit=delivery_unit,
        repeated_fraction_information={
            "integrated_same_fraction": integrated,
            "local_fraction_index": local_index or biological_index,
            "source_methods": [item["method"] for item in prepared],
            "declared_fraction_counts": [item["fraction_count"] for item in prepared],
        },
        provenance={
            "source_layer1_results": [source["result_path"] for source in sources],
            "source_layer1_hashes": [source["result_hash"] for source in sources],
            "physical_components_summed_before_transformation": integrated,
        },
    )

