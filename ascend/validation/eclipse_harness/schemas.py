"""Versioned records and acceptance criteria for formal Eclipse comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


REFERENCE_SCHEMA_VERSION = "ASCEND-ECLIPSE-DVH-REFERENCE-v1"
COMPARISON_SCHEMA_VERSION = "ASCEND-ECLIPSE-DVH-COMPARISON-v1"
SUMMARY_SCHEMA_VERSION = "ASCEND-ECLIPSE-DVH-SUMMARY-v1"
ACCEPTANCE_CRITERION_VERSION = "ASCEND-ECLIPSE-SOFTWARE-AGREEMENT-v1"

PLANNED_ENDPOINTS = (
    "D2", "D5", "D50", "D90", "D95", "D98", "Dmean", "V95%Rx", "V100%Rx",
)

MATCHING_STATUSES = (
    "matched_exact_identity",
    "matched_unique_fallback",
    "ambiguous",
    "not_found",
    "identity_conflict",
)

COMPARISON_STATUSES = (
    "valid_comparison",
    "not_comparable",
    "ambiguous_structure",
    "missing_ascend_endpoint",
    "missing_eclipse_endpoint",
    "unit_mismatch",
    "identity_conflict",
    "invalid_reference",
)


class ReferenceImportError(ValueError):
    """The supplied Eclipse reference cannot be interpreted unambiguously."""


@dataclass(frozen=True)
class AcceptanceCriteria:
    """Represent acceptance criteria state and behavior."""
    version: str = ACCEPTANCE_CRITERION_VERSION
    dose_absolute_floor_gy: float = 0.2
    dose_relative_fraction: float = 0.02
    percentage_volume_limit_points: float = 1.0
    structure_volume_absolute_floor_cc: float = 0.1
    structure_volume_relative_fraction: float = 0.02
    relative_difference_zero_epsilon: float = 1e-12
    small_structure_upper_cc: float = 10.0
    medium_structure_upper_cc: float = 100.0

    def __post_init__(self) -> None:
        positive = {
            "dose_absolute_floor_gy": self.dose_absolute_floor_gy,
            "dose_relative_fraction": self.dose_relative_fraction,
            "percentage_volume_limit_points": self.percentage_volume_limit_points,
            "structure_volume_absolute_floor_cc": self.structure_volume_absolute_floor_cc,
            "structure_volume_relative_fraction": self.structure_volume_relative_fraction,
            "relative_difference_zero_epsilon": self.relative_difference_zero_epsilon,
            "small_structure_upper_cc": self.small_structure_upper_cc,
            "medium_structure_upper_cc": self.medium_structure_upper_cc,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"Acceptance criteria must be positive: {', '.join(invalid)}")
        if self.medium_structure_upper_cc <= self.small_structure_upper_cc:
            raise ValueError("The medium structure upper bound must exceed the small structure upper bound.")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        return {
            "version": self.version,
            "dose_endpoints": {
                "rule": "abs(ASCEND - Eclipse) <= max(absolute_floor_gy, relative_fraction * abs(Eclipse))",
                "absolute_floor_gy": self.dose_absolute_floor_gy,
                "relative_fraction": self.dose_relative_fraction,
            },
            "percentage_volume_endpoints": {
                "rule": "abs(ASCEND - Eclipse) <= limit_percentage_points",
                "limit_percentage_points": self.percentage_volume_limit_points,
            },
            "structure_volume": {
                "rule": "abs(ASCEND - Eclipse) <= max(absolute_floor_cc, relative_fraction * reference_volume_cc)",
                "absolute_floor_cc": self.structure_volume_absolute_floor_cc,
                "relative_fraction": self.structure_volume_relative_fraction,
            },
            "relative_difference_zero_epsilon": self.relative_difference_zero_epsilon,
            "structure_size_bins_cc": {
                "small": f"volume < {self.small_structure_upper_cc:g}",
                "medium": f"{self.small_structure_upper_cc:g} <= volume < {self.medium_structure_upper_cc:g}",
                "large": f"volume >= {self.medium_structure_upper_cc:g}",
                "purpose": "validation stratification only; not a clinical classification",
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AcceptanceCriteria":
        """Construct this record from dict."""
        dose = value.get("dose_endpoints", {})
        percentage = value.get("percentage_volume_endpoints", {})
        volume = value.get("structure_volume", {})
        bins = value.get("structure_size_bins_cc", {})
        return cls(
            version=str(value.get("version", ACCEPTANCE_CRITERION_VERSION)),
            dose_absolute_floor_gy=float(dose.get("absolute_floor_gy", value.get("dose_absolute_floor_gy", 0.2))),
            dose_relative_fraction=float(dose.get("relative_fraction", value.get("dose_relative_fraction", 0.02))),
            percentage_volume_limit_points=float(
                percentage.get("limit_percentage_points", value.get("percentage_volume_limit_points", 1.0))
            ),
            structure_volume_absolute_floor_cc=float(
                volume.get("absolute_floor_cc", value.get("structure_volume_absolute_floor_cc", 0.1))
            ),
            structure_volume_relative_fraction=float(
                volume.get("relative_fraction", value.get("structure_volume_relative_fraction", 0.02))
            ),
            relative_difference_zero_epsilon=float(value.get("relative_difference_zero_epsilon", 1e-12)),
            small_structure_upper_cc=float(bins.get("small_upper_cc", value.get("small_structure_upper_cc", 10.0))),
            medium_structure_upper_cc=float(bins.get("medium_upper_cc", value.get("medium_structure_upper_cc", 100.0))),
        )

    def size_class(self, volume_cc: float | None) -> str:
        """Handle size class for the enclosing ASCEND workflow."""
        if volume_cc is None:
            return "unknown"
        if volume_cc < self.small_structure_upper_cc:
            return "small"
        if volume_cc < self.medium_structure_upper_cc:
            return "medium"
        return "large"


@dataclass
class ReferenceRecord:
    """Represent reference record state and behavior."""
    case_id: str
    rtstruct_uid: str | None
    rtdose_uid: str | None
    rtplan_uid: str | None
    roi_number: int | None
    roi_name: str
    endpoint: str
    endpoint_type: str
    eclipse_value: float | None
    units: str
    rx_gy: float | None = None
    reference_volume_cc: float | None = None
    structure_role: str | None = None
    eclipse_software: str | None = None
    eclipse_version: str | None = None
    source_file: str = ""
    source_content_hash: str = ""
    import_timestamp_utc: str = ""
    import_status: str = "valid"
    import_reason: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def structure_identity(self) -> dict[str, Any]:
        """Handle structure identity for the enclosing ASCEND workflow."""
        return {
            "rtstruct_sop_instance_uid": self.rtstruct_uid,
            "roi_number": self.roi_number,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        value = asdict(self)
        value["structure_identity"] = self.structure_identity
        return value


@dataclass
class MatchResult:
    """Represent match result state and behavior."""
    status: str
    candidate: dict[str, Any] | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        return asdict(self)
