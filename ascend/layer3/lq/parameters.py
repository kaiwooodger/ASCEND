"""Validation and parsing of Layer 3.1 tissue parameters and sensitivity sweeps."""

from __future__ import annotations

import math
from typing import Any

from ascend.dicom.roi import validate_identity


PARAMETER_SOURCE_TYPES = ("configured_reference", "user_selected", "imported_parameter_set")


def validate_alpha_beta(value: Any) -> float:
    """Validate alpha beta and raise a controlled error when requirements are not met."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("alpha_beta_gy must be numeric.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError("alpha_beta_gy must be finite and greater than zero.")
    return result


def validate_parameter_assignment(value: dict[str, Any], label: str = "ROI parameter") -> None:
    """Validate parameter assignment and raise a controlled error when requirements are not met."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    validate_identity(value.get("roi_identity"), label)
    validate_alpha_beta(value.get("alpha_beta_gy"))
    source_type = str(value.get("parameter_source_type") or "")
    if source_type not in PARAMETER_SOURCE_TYPES:
        raise ValueError(f"{label} has unsupported parameter_source_type {source_type!r}.")
    if not str(value.get("parameter_source") or "").strip():
        raise ValueError(f"{label} requires parameter_source.")
    if not str(value.get("parameter_set_version") or "").strip():
        raise ValueError(f"{label} requires parameter_set_version.")


def parse_sweep(values: Any) -> list[float]:
    """Parse sweep using the documented input contract."""
    raw = values.split(",") if isinstance(values, str) else list(values or [])
    parsed = [validate_alpha_beta(item.strip() if isinstance(item, str) else item) for item in raw if str(item).strip()]
    if not parsed:
        raise ValueError("At least one alpha/beta value is required for a parameter sweep.")
    return parsed

