"""Typed records for treatment, case, biological, or validation state."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any


TREATMENT_APPROACHES = ("LRT_ALONE", "LRT_SEQUENTIAL_CERT", "LRT_INTEGRATED", "UNKNOWN")
COMPONENT_TYPES = (
    "LRT", "CERT", "OTHER",
    "lrt", "conventional_rt", "integrated_plan", "composite_course", "unknown",
)
COMPONENT_SOURCES = (
    "RTPLAN", "protocol_configuration", "user_supplied",
    "synthetic_validation", "derived_composite", "unknown",
)


def _freeze(value: Any) -> Any:
    """Recursively freeze nested provenance so a context hash cannot drift after construction."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return a detached JSON-compatible representation of frozen nested state."""
    if isinstance(value, dict) or isinstance(value, MappingProxyType):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class TreatmentComponent:
    """Represent treatment component state and behavior."""
    component_id: str
    component_type: str
    dose_object_uid: str | None = None
    plan_uid: str | None = None
    fraction_count: int | None = None
    prescription_gy: float | None = None
    rx_low_gy: float | None = None
    rx_high_gy: float | None = None
    source: str = "unknown"
    start_time: str | None = None
    end_time: str | None = None
    preceding_gap_days: float | None = None
    geometry_id: str | None = None
    geometry_hash: str | None = None
    prescription_source: str | None = None
    delivery_time: float | None = None
    delivery_time_unit: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ValueError("Treatment component ID must be non-empty.")
        if self.component_type not in COMPONENT_TYPES:
            raise ValueError(f"Unsupported treatment component type: {self.component_type}")
        if self.source not in COMPONENT_SOURCES:
            raise ValueError(f"Unsupported treatment component source: {self.source}")
        if self.fraction_count is not None and self.fraction_count <= 0:
            raise ValueError("Treatment component fraction_count must be positive.")
        for field_name in ("prescription_gy", "rx_low_gy", "rx_high_gy"):
            value = getattr(self, field_name)
            if value is not None and (not math.isfinite(float(value)) or float(value) <= 0):
                raise ValueError(f"Treatment component {field_name} must be finite and positive.")
        if self.preceding_gap_days is not None and (
            not math.isfinite(float(self.preceding_gap_days)) or float(self.preceding_gap_days) < 0
        ):
            raise ValueError("Treatment component preceding_gap_days must be finite and non-negative.")
        if self.prescription_source is not None and not str(self.prescription_source).strip():
            raise ValueError("Treatment component prescription_source must be non-empty when supplied.")
        if self.delivery_time is not None and (
            not math.isfinite(float(self.delivery_time)) or float(self.delivery_time) < 0
        ):
            raise ValueError("Treatment component delivery_time must be finite and non-negative.")
        if self.delivery_time_unit is not None and self.delivery_time_unit not in {"seconds", "minutes", "hours"}:
            raise ValueError("Treatment component delivery_time_unit must be seconds, minutes, or hours.")
        if (self.delivery_time is None) != (self.delivery_time_unit is None):
            raise ValueError("Treatment component delivery time and unit must be supplied together.")
        object.__setattr__(self, "provenance", _freeze(dict(self.provenance)))

    @property
    def dose_per_fraction_gy(self) -> float | None:
        """Handle dose per fraction gy for the enclosing ASCEND workflow."""
        if self.prescription_gy is None or self.fraction_count is None:
            return None
        return float(self.prescription_gy) / self.fraction_count

    @property
    def number_of_fractions(self) -> int | None:
        """Return the explicit component fraction count using specification terminology."""
        return self.fraction_count

    @property
    def dose_source_uid(self) -> str | None:
        """Return the dose object identity using specification terminology."""
        return self.dose_object_uid

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        return {item.name: _thaw(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TreatmentComponent":
        """Construct this record from dict."""
        if not isinstance(value, dict):
            raise ValueError("Treatment component must be a JSON object.")
        return cls(**value)


@dataclass(frozen=True)
class TreatmentContext:
    """Represent treatment context state and behavior."""
    treatment_delivery_mode: str
    dose_context: str
    prescription_context: str
    dose_object_uid: str | None
    plan_uid: str | None
    selected_component: TreatmentComponent | None
    components: tuple[TreatmentComponent, ...] = field(default_factory=tuple)
    prescriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    valley_includes_cert_background: bool | None = None
    treatment_approach: str = "UNKNOWN"
    analysis_component_id: str | None = None
    protocol_id: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        approach = self.treatment_approach
        if approach == "UNKNOWN":
            approach = {
                "simultaneous_integrated_lrt": "LRT_INTEGRATED",
                "integrated_lrt_cert": "LRT_INTEGRATED",
                "sequential_lrt_boost": "LRT_SEQUENTIAL_CERT",
                "partial_volume_lrt": "LRT_ALONE",
            }.get(self.treatment_delivery_mode, "UNKNOWN")
        if approach not in TREATMENT_APPROACHES:
            raise ValueError(f"Unsupported treatment approach: {approach}")
        object.__setattr__(self, "treatment_approach", approach)
        if self.analysis_component_id is None and self.selected_component is not None:
            object.__setattr__(self, "analysis_component_id", self.selected_component.component_id)
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "prescriptions", _freeze(dict(self.prescriptions)))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "provenance", _freeze(dict(self.provenance)))

    @property
    def context_hash(self) -> str:
        """Return a deterministic content hash for downstream provenance."""
        encoded = json.dumps(self.to_dict(include_hash=False), sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self, include_hash: bool = True) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = {
            "schema_version": "ASCEND-TreatmentContext-v2",
            "treatment_approach": self.treatment_approach,
            "treatment_delivery_mode": self.treatment_delivery_mode,
            "dose_context": self.dose_context,
            "prescription_context": self.prescription_context,
            "dose_object_uid": self.dose_object_uid,
            "plan_uid": self.plan_uid,
            "selected_component_id": self.selected_component.component_id if self.selected_component else None,
            "analysis_component_id": self.analysis_component_id,
            "components": [item.to_dict() for item in self.components],
            "prescriptions": _thaw(self.prescriptions),
            "valley_includes_cert_background": self.valley_includes_cert_background,
            "protocol_id": self.protocol_id,
            "warnings": list(self.warnings),
            "provenance": _thaw(self.provenance),
        }
        if include_hash:
            value["treatment_context_hash"] = self.context_hash
        return value

    @classmethod
    def from_case(cls, configuration: Any, manifest: dict[str, Any]) -> "TreatmentContext":
        """Construct this record from case."""
        components = tuple(TreatmentComponent.from_dict(item) for item in configuration.treatment_components)
        selected = next(
            (item for item in components if item.component_id == configuration.selected_treatment_component_id),
            None,
        )
        prescriptions = {
            key: {
                "gy": value.gy,
                "fractions": value.fractions,
                "source": value.source,
            }
            for key, value in configuration.prescriptions.items()
        }
        return cls(
            treatment_delivery_mode=configuration.treatment_delivery_mode,
            dose_context=configuration.dose_context,
            prescription_context=configuration.prescription_context,
            dose_object_uid=manifest.get("rtdose_uid"),
            plan_uid=manifest.get("rtplan_uid"),
            selected_component=selected,
            components=components,
            prescriptions=prescriptions,
            valley_includes_cert_background=configuration.valley_includes_cert_background,
            treatment_approach=getattr(configuration, "treatment_approach", "UNKNOWN"),
            analysis_component_id=configuration.selected_treatment_component_id,
            protocol_id=configuration.protocol_id,
            warnings=tuple(
                ["treatment_approach_unknown"]
                if getattr(configuration, "treatment_approach", "UNKNOWN") == "UNKNOWN"
                and configuration.treatment_delivery_mode == "unknown" else []
            ),
            provenance={
                "source": "validated_case_configuration_and_layer1_manifest",
                "dose_object_uid": manifest.get("rtdose_uid"),
                "plan_uid": manifest.get("rtplan_uid"),
                "implicit_registration": False,
                "implicit_dose_warping": False,
                "time_effects_modelled": False,
            },
        )
