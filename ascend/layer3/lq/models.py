"""Typed records for treatment, case, biological, or validation state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


LQ_ALGORITHM_VERSION = "ASCEND-L3.1-LQ-PQ-v1.0"
LQ_BASIS_SCHEMA_VERSION = "ASCEND-L3.1-PQ-basis-v2"
LQ_RESULT_SCHEMA_VERSION = "ASCEND-L3.1-result-v5"
LQ_CACHE_SCHEMA_VERSION = "ASCEND-L3.1-cache-v2"
FUNDAMENTAL_PQ_MODEL = {
    "P": "P(x) = sum_f d_f(x)",
    "Q": "Q(x) = sum_f d_f(x)^2",
    "identical_fraction_component_shortcut": {
        "assumption": "component k consists of n_k identical fractions",
        "P_k": "D_k(x)",
        "Q_k": "D_k(x)^2 / n_k",
    },
}


@dataclass(frozen=True)
class ROIParameterAssignment:
    """Represent r o i parameter assignment state and behavior."""
    roi_identity: dict[str, Any]
    roi_name: str
    canonical_role: str | None
    alpha_beta_gy: float
    parameter_source: str
    parameter_source_type: str
    parameter_set_version: str
    assignment_method: str
    assignment_origin: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        return value


@dataclass(frozen=True)
class ComponentEvidence:
    """Represent component evidence state and behavior."""
    component_id: str
    dose_uid: str
    plan_uid: str | None
    fraction_count: int
    prescription_gy: float | None
    prescription_source: str
    dose_sha256: str
    layer1_result_sha256: str
    layer1_result_path: str
    geometry_hash: str
    treatment_component_type: str
    accumulation_method: str = "identical_fraction_component_total_shortcut"
    fraction_dose_uids: tuple[str, ...] = field(default_factory=tuple)
    fraction_dose_sha256: tuple[str, ...] = field(default_factory=tuple)
    timepoint: str = "unspecified"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = asdict(self)
        value["fraction_dose_uids"] = list(self.fraction_dose_uids)
        value["fraction_dose_sha256"] = list(self.fraction_dose_sha256)
        return value


@dataclass(frozen=True)
class ROIInstance:
    """One validated ROI state at a component/timepoint on the common physical grid."""

    roi_identity: dict[str, Any]
    roi_name: str
    canonical_role: str | None
    reference_geometry: str
    treatment_component: str
    timepoint: str
    volume_cc: float
    mask: dict[str, Any]
    mask_sha256: str
    mask_key: str
    source_layer1_result_sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        return asdict(self)


@dataclass
class LQBiologicalBasis:
    """Represent l q biological basis state and behavior."""
    geometry_identity: str
    dose_grid_shape: tuple[int, int, int]
    dose_grid_spacing_mm: tuple[float, float, float]
    frame_of_reference_uid: str
    components: tuple[ComponentEvidence, ...]
    roi_history: tuple[ROIInstance, ...]
    p_map: np.ndarray
    q_map: np.ndarray
    dtype: str
    algorithm_version: str
    configuration_hash: str
    source_hashes: dict[str, str]
    warnings: tuple[str, ...]
    provenance: dict[str, Any]
    basis_hash: str
    cache_key: str
    cache_hit: bool
    cache_path: str | None = None

    def metadata(self) -> dict[str, Any]:
        """Handle metadata for the enclosing ASCEND workflow."""
        return {
            "geometry_identity": self.geometry_identity,
            "dose_grid_shape": list(self.dose_grid_shape),
            "dose_grid_spacing_mm": list(self.dose_grid_spacing_mm),
            "frame_of_reference_uid": self.frame_of_reference_uid,
            "component_ids": [item.component_id for item in self.components],
            "component_dose_uids": [item.dose_uid for item in self.components],
            "component_plan_uids": [item.plan_uid for item in self.components],
            "component_fraction_counts": [item.fraction_count for item in self.components],
            "components": [item.to_dict() for item in self.components],
            "roi_history": [item.to_dict() for item in self.roi_history],
            "dtype": self.dtype,
            "algorithm_version": self.algorithm_version,
            "configuration_hash": self.configuration_hash,
            "source_hashes": self.source_hashes,
            "warnings": list(self.warnings),
            "provenance": self.provenance,
            "basis_hash": self.basis_hash,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
            "cache_path": self.cache_path,
            "units": {"P": "Gy", "Q": "Gy^2"},
            "fundamental_model": FUNDAMENTAL_PQ_MODEL,
            "geometry_policy": {
                "course_accumulation": "same_validated_physical_geometry_only",
                "implicit_registration": False,
                "implicit_dose_warping": False,
            },
        }


@dataclass(frozen=True)
class BasisBuildResult:
    """Represent basis build result state and behavior."""
    calculation_status: str
    interpretation_status: str
    reason: str | None
    warnings: tuple[str, ...]
    basis: LQBiologicalBasis | None


@dataclass(frozen=True)
class Layer31ROIResult:
    """Represent layer31 r o i result state and behavior."""
    calculation_status: str
    interpretation_status: str
    assignment: ROIParameterAssignment
    metrics: dict[str, float]
    bed_volume_histogram: dict[str, Any]
    eqd2_volume_histogram: dict[str, Any]
    voxel_count: int
    dose_sampled_volume_cc: float
    warnings: tuple[str, ...]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        return value


@dataclass(frozen=True)
class Layer31SweepResult:
    """Represent layer31 sweep result state and behavior."""
    calculation_status: str
    interpretation_status: str
    basis_hash: str
    basis_cache_hit: bool
    roi_identity: dict[str, Any]
    parameter_source: str
    records: tuple[dict[str, float], ...]
    warnings: tuple[str, ...]
    algorithm_version: str = LQ_ALGORITHM_VERSION
    schema_version: str = "ASCEND-L3.1-sweep-v1"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = asdict(self)
        value["records"] = list(self.records)
        value["warnings"] = list(self.warnings)
        return value

