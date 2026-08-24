"""Validated user and DICOM-derived configuration contracts for an ASCEND case."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from ascend.dicom.roi import validate_identity


TREATMENT_DELIVERY_MODES = (
    "simultaneous_integrated_lrt", "integrated_lrt_cert",
    "sequential_lrt_boost", "composite_course", "conventional_only",
    "partial_volume_lrt", "other", "unknown",
)
TREATMENT_APPROACHES = ("LRT_ALONE", "LRT_SEQUENTIAL_CERT", "LRT_INTEGRATED", "UNKNOWN")
DOSE_CONTEXTS = (
    "lrt_component", "conventional_component", "complete_single_plan",
    "composite_course", "unknown",
)
PRESCRIPTION_CONTEXTS = (
    "component_specific", "complete_plan", "composite_course", "manual", "unknown",
)
PRESCRIPTION_SOURCES = ("RTPLAN", "protocol_configuration", "user_supplied", "unavailable")
PROTOCOL_ENDPOINT_KINDS = ("coverage_relative_rx", "coverage_absolute_gy", "d_percent")
PROTOCOL_ENDPOINT_ROLES = ("GTV", "T_L", "VTV_H", "VTV_L")
OAR_CLASSIFICATIONS = (
    "containing_organ", "target_excluded_oar", "separate_critical_oar", "internal_target_structure",
)
SUPPORTING_OUTPUT_CATEGORIES = (
    "coverage", "peak_valley", "per_vertex", "protocol_native", "oar_geometry", "integrity",
)

LAYER31_TUMOUR_SCENARIOS = ("C1", "C2", "C3")
LAYER31_NORMAL_SCENARIOS = ("N1", "N2", "N3")


@dataclass
class Prescription:
    """Represent prescription state and behavior."""
    gy: float | None = None
    fractions: int | None = None
    source: str = "unavailable"

    def validate(self) -> None:
        """Validate validate and raise a controlled error when requirements are not met."""
        if self.gy is not None and self.gy <= 0:
            raise ValueError("Prescription dose must be greater than zero.")
        if self.fractions is not None and self.fractions <= 0:
            raise ValueError("Fractions must be greater than zero.")
        if self.source not in PRESCRIPTION_SOURCES:
            raise ValueError(f"Unsupported prescription source: {self.source}")


@dataclass
class CaseConfiguration:
    """Represent case configuration state and behavior."""
    treatment_delivery_mode: str = "unknown"
    treatment_approach: str = "UNKNOWN"
    dose_context: str = "unknown"
    prescription_context: str = "unknown"
    treatment_components: list[dict[str, Any]] = field(default_factory=list)
    selected_treatment_component_id: str | None = None
    valley_includes_cert_background: bool | None = None
    prescriptions: dict[str, Prescription] = field(default_factory=lambda: {
        "Rx_L": Prescription(), "Rx_H": Prescription(),
    })
    fractionation: dict[str, Any] = field(default_factory=dict)
    structure_roles: dict[str, str | list[str]] = field(default_factory=dict)
    structure_bindings: dict[str, dict[str, Any] | list[dict[str, Any]]] = field(default_factory=dict)
    validation_structures: list[dict[str, Any]] = field(default_factory=list)
    protocol_id: str | None = None
    protocol_context: dict[str, bool] = field(default_factory=lambda: {
        "prescriptions_confirmed": False,
        "roles_confirmed": False,
        "dose_object_confirmed": False,
        "valley_confirmed": False,
    })
    protocol_native_endpoints: list[dict[str, Any]] = field(default_factory=list)
    oar_structures: list[dict[str, str]] = field(default_factory=list)
    equal_prescriptions_protocol_confirmed: bool = False
    partial_volume_only: bool = False
    valley_definition_source: str = "validated Layer 1 structure"
    valley_overlap_tolerance_pct: float = 0.0
    tps_metrics_csv: str | None = None
    supporting_outputs_enabled: bool = True
    supporting_output_categories: list[str] = field(default_factory=lambda: list(SUPPORTING_OUTPUT_CATEGORIES))
    layer31_roi_parameters: list[dict[str, Any]] = field(default_factory=list)
    layer31_component_sources: list[dict[str, Any]] = field(default_factory=list)
    layer31_lq_high_dose_warning_gy_per_fraction: float | None = None
    layer31_mlq_tumour_parameters: dict[str, Any] = field(default_factory=dict)
    layer31_mlq_normal_parameters: dict[str, Any] = field(default_factory=dict)
    layer31_tumour_scenario: str | None = None
    layer31_normal_scenario: str | None = None
    layer31_tr_reference_schedule: dict[str, Any] = field(default_factory=dict)
    layer31_paired_course_reference_result_path: str | None = None
    layer31_tcp_parameters: dict[str, Any] = field(default_factory=dict)
    layer31_visualisation_settings: dict[str, Any] = field(default_factory=lambda: {
        "method": "taubin_non_shrinking", "iterations": 12, "lambda": 0.25, "mu": -0.27,
    })
    # Large voxel fields are presentation/export artifacts, not prerequisites
    # for scalar Layer 3.1 results.  The GUI materialises them on demand.
    layer31_materialise_full_maps_on_run: bool = False
    layer31_sensitivity_sweep_enabled: bool = False
    layer31_sensitivity_sweep_mode: str = "standard"
    layer31_sensitivity_sweep_start: float = 2.0
    layer31_sensitivity_sweep_end: float = 10.0
    layer31_sensitivity_sweep_custom_values: str = "2,3,5,8,10"
    # Layer 3.2 is an optional research reinterpretation and must be explicitly
    # enabled before it can participate in calculation, presentation, or export.
    layer32_enabled: bool = False
    layer32_parameters: dict[str, Any] = field(default_factory=dict)
    eclipse_endpoint_prefill: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate validate and raise a controlled error when requirements are not met."""
        if self.treatment_delivery_mode not in TREATMENT_DELIVERY_MODES:
            raise ValueError(f"Unsupported treatment delivery mode: {self.treatment_delivery_mode}")
        if self.treatment_approach not in TREATMENT_APPROACHES:
            raise ValueError(f"Unsupported treatment approach: {self.treatment_approach}")
        if self.dose_context not in DOSE_CONTEXTS:
            raise ValueError(f"Unsupported dose context: {self.dose_context}")
        if self.prescription_context not in PRESCRIPTION_CONTEXTS:
            raise ValueError(f"Unsupported prescription context: {self.prescription_context}")
        from ascend.treatment.models import TreatmentComponent
        components = [TreatmentComponent.from_dict(item) for item in self.treatment_components]
        component_ids = [item.component_id for item in components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("Treatment component IDs must be unique.")
        if self.selected_treatment_component_id is not None and self.selected_treatment_component_id not in component_ids:
            raise ValueError("Selected treatment component ID is not present in treatment_components.")
        for prescription in self.prescriptions.values():
            prescription.validate()
        for role, binding in self.structure_bindings.items():
            values = binding if isinstance(binding, list) else [binding]
            for index, item in enumerate(values, 1):
                validate_identity(item, f"Structure binding {role}[{index}]")
        for index, item in enumerate(self.validation_structures, 1):
            validate_identity(item, f"Validation structure {index}")
        endpoint_ids: set[str] = set()
        for index, endpoint in enumerate(self.protocol_native_endpoints, 1):
            if not isinstance(endpoint, dict):
                raise ValueError(f"Protocol-native endpoint {index} must be a structured endpoint record.")
            endpoint_id = str(endpoint.get("id") or "").strip()
            if not endpoint_id or endpoint_id in endpoint_ids:
                raise ValueError(f"Protocol-native endpoint {index} requires a unique non-empty id.")
            endpoint_ids.add(endpoint_id)
            if endpoint.get("role") not in PROTOCOL_ENDPOINT_ROLES:
                raise ValueError(f"Protocol-native endpoint {endpoint_id} has unsupported role {endpoint.get('role')!r}.")
            kind = endpoint.get("kind")
            if kind not in PROTOCOL_ENDPOINT_KINDS:
                raise ValueError(f"Protocol-native endpoint {endpoint_id} has unsupported kind {kind!r}.")
            try:
                value = float(endpoint["value"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Protocol-native endpoint {endpoint_id} requires a numeric value.") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Protocol-native endpoint {endpoint_id} value must be finite and greater than zero.")
            if kind == "d_percent" and value > 100:
                raise ValueError(f"Protocol-native endpoint {endpoint_id} D-percent value must not exceed 100.")
        oar_names: set[str] = set()
        for index, item in enumerate(self.oar_structures, 1):
            if not isinstance(item, dict):
                raise ValueError(f"OAR geometry entry {index} must be a structured OAR record.")
            name = str(item.get("name") or item.get("display_name") or "").strip()
            if not name or name in oar_names:
                raise ValueError(f"OAR geometry entry {index} requires a unique non-empty RTSTRUCT name.")
            oar_names.add(name)
            if item.get("classification") not in OAR_CLASSIFICATIONS:
                raise ValueError(
                    f"OAR geometry entry {name} has unsupported classification {item.get('classification')!r}."
                )
            if item.get("roi_identity") is not None:
                validate_identity(item["roi_identity"], f"OAR geometry entry {name}")
        invalid_support = sorted(set(self.supporting_output_categories) - set(SUPPORTING_OUTPUT_CATEGORIES))
        if invalid_support:
            raise ValueError(f"Unsupported supporting-output categories: {', '.join(invalid_support)}")
        from ascend.layer3.lq.parameters import validate_parameter_assignment
        for assignment in self.layer31_roi_parameters:
            validate_parameter_assignment(assignment)
        if self.layer31_lq_high_dose_warning_gy_per_fraction is not None:
            threshold = float(self.layer31_lq_high_dose_warning_gy_per_fraction)
            if not math.isfinite(threshold) or threshold <= 0:
                raise ValueError("Layer 3.1 LQ high-dose warning threshold must be finite and positive.")
        if self.layer31_tumour_scenario is not None and self.layer31_tumour_scenario not in LAYER31_TUMOUR_SCENARIOS:
            raise ValueError("Layer 3.1 tumour scenario must be C1, C2, or C3.")
        if self.layer31_normal_scenario is not None and self.layer31_normal_scenario not in LAYER31_NORMAL_SCENARIOS:
            raise ValueError("Layer 3.1 normal-cell scenario must be N1, N2, or N3.")
        schedule = self.layer31_tr_reference_schedule
        if not isinstance(schedule, dict):
            raise ValueError("Layer 3.1 comparator schedule must be a structured record.")
        if schedule:
            count = schedule.get("fraction_count")
            if isinstance(count, bool):
                raise ValueError("Layer 3.1 comparator fraction count must be a positive integer.")
            try:
                numeric_count = float(count)
            except (TypeError, ValueError) as exc:
                raise ValueError("Layer 3.1 comparator fraction count must be a positive integer.") from exc
            if not math.isfinite(numeric_count) or numeric_count <= 0 or not numeric_count.is_integer():
                raise ValueError("Layer 3.1 comparator fraction count must be a positive integer.")
            delivery_time = schedule.get("delivery_time")
            delivery_times = schedule.get("delivery_times")
            if delivery_time is not None and delivery_times is not None:
                raise ValueError("Layer 3.1 comparator must define delivery_time or delivery_times, not both.")
            if delivery_time is not None:
                try:
                    value = float(delivery_time)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Layer 3.1 comparator delivery time must be finite and non-negative.") from exc
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Layer 3.1 comparator delivery time must be finite and non-negative.")
            if delivery_times is not None:
                if not isinstance(delivery_times, list) or len(delivery_times) != int(numeric_count):
                    raise ValueError("Layer 3.1 comparator delivery-time list must match the fraction count.")
                try:
                    values = [float(item) for item in delivery_times]
                except (TypeError, ValueError) as exc:
                    raise ValueError("Layer 3.1 comparator delivery times must be finite and non-negative.") from exc
                if any(not math.isfinite(item) or item < 0 for item in values):
                    raise ValueError("Layer 3.1 comparator delivery times must be finite and non-negative.")
        if self.layer31_paired_course_reference_result_path is not None:
            if not isinstance(self.layer31_paired_course_reference_result_path, str) or not self.layer31_paired_course_reference_result_path.strip():
                raise ValueError("Layer 3.1 paired-course reference result path must be a non-empty path.")
        if not isinstance(self.layer31_tcp_parameters, dict):
            raise ValueError("Layer 3.1D TCP parameters must be a structured record.")
        if self.layer31_tcp_parameters:
            density = self.layer31_tcp_parameters.get("clonogen_density_per_cm3")
            try:
                density_value = float(density)
            except (TypeError, ValueError) as exc:
                raise ValueError("Layer 3.1D clonogen density must be numeric.") from exc
            if not math.isfinite(density_value) or density_value <= 0:
                raise ValueError("Layer 3.1D clonogen density must be finite and positive.")
            if str(self.layer31_tcp_parameters.get("units") or "") not in {"clonogens/cm3", "clonogens/cm^3"}:
                raise ValueError("Layer 3.1D clonogen density units must be clonogens/cm3.")
            for key in ("source", "parameter_set_id"):
                if not str(self.layer31_tcp_parameters.get(key) or "").strip():
                    raise ValueError(f"Layer 3.1D {key} is required.")
            if self.layer31_tcp_parameters.get("repopulation_enabled"):
                for key, positive in (("overall_treatment_time_days", False), ("kickoff_time_days", False), ("potential_doubling_time_days", True)):
                    try:
                        value = float(self.layer31_tcp_parameters[key])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(f"Layer 3.1D {key} is required and must be numeric.") from exc
                    if not math.isfinite(value) or value < 0 or (positive and value == 0):
                        qualifier = "positive" if positive else "non-negative"
                        raise ValueError(f"Layer 3.1D {key} must be finite and {qualifier}.")
            if self.layer31_tcp_parameters.get("sensitivity_enabled"):
                values = self.layer31_tcp_parameters.get("sensitivity_clonogen_density_values")
                if not isinstance(values, list) or not values:
                    raise ValueError("Layer 3.1D sensitivity requires clonogen-density values.")
                if any(not math.isfinite(float(item)) or float(item) <= 0 for item in values):
                    raise ValueError("Layer 3.1D sensitivity clonogen densities must be finite and positive.")
        if self.layer31_sensitivity_sweep_mode not in {"standard", "step_1", "step_2", "custom"}:
            raise ValueError("Unsupported Layer 3.1 sensitivity-sweep mode.")
        if not isinstance(self.layer31_visualisation_settings, dict):
            raise ValueError("Layer 3.1 visualisation settings must be a structured record.")
        if not isinstance(self.layer32_enabled, bool):
            raise ValueError("Layer 3.2 enabled state must be true or false.")
        # Layer 3.2 uses a strict allow-list.  In particular, vessel geometry,
        # vascular modes, and uptake coefficients cannot enter configuration.
        from ascend.layer3.nonlocal_effect.models import resolved_parameters
        resolved_parameters(self.layer32_parameters)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CaseConfiguration":
        """Construct this record from dict."""
        data = dict(value)
        raw = data.pop("prescriptions", {})
        prescriptions = {
            key: item if isinstance(item, Prescription) else Prescription(**item)
            for key, item in raw.items()
        }
        prescriptions.setdefault("Rx_L", Prescription())
        prescriptions.setdefault("Rx_H", Prescription())
        return cls(prescriptions=prescriptions, **data)
