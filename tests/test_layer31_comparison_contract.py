from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from ascend.layer3.response.comparison import compare_course_results
from ascend.models.config import CaseConfiguration


def _result(approach: str, sf: float, eud: float) -> dict:
    return {
        "run_id": f"run-{approach}",
        "treatment_context": {"treatment_approach": approach},
        "basis": {"geometry_identity": "geometry-1"},
        "layer3_1b_high_dose_sfrt_response": {
            "applicability_status": "APPLICABLE", "parameter_hash": "parameters-1",
            "mask_hash": "mask-1", "mean_tumour_survival_fraction": sf, "tumour_eud_gy": eud,
            "reference_schedule": {"fraction_count": 1, "delivery_times": [10.0], "time_unit": "minutes"},
        },
    }


def test_paired_course_comparison_requires_and_records_all_identity_gates() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "reference.json"; reference = _result("LRT_ALONE", 0.3, 12.0)
        path.write_text(json.dumps(reference))
        result = compare_course_results(_result("LRT_SEQUENTIAL_CERT", 0.2, 16.0), reference, path)
        assert result["applicability_status"] == "APPLICABLE"
        assert all(item["status"] == "PASS" for item in result["gate_results"])
        assert result["comparison"]["eud_difference_gy_lrt_plus_cert_minus_lrt"] == 4.0


def test_paired_course_comparison_blocks_parameter_mismatch() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "reference.json"; reference = _result("LRT_ALONE", 0.3, 12.0)
        path.write_text(json.dumps(reference))
        current = _result("LRT_SEQUENTIAL_CERT", 0.2, 16.0)
        current["layer3_1b_high_dose_sfrt_response"]["parameter_hash"] = "different"
        result = compare_course_results(current, reference, path)
        assert result["status"] == "BLOCKED"
        assert "PAIRED_TUMOUR_PARAMETER_SET_MISMATCH" in result["blocking_reasons"]


@pytest.mark.parametrize("schedule", [
    {"fraction_count": 0, "delivery_time": 1.0},
    {"fraction_count": -1, "delivery_time": 1.0},
    {"fraction_count": 2, "delivery_time": float("nan")},
    {"fraction_count": 2, "delivery_time": -1.0},
    {"fraction_count": 2, "delivery_times": [1.0]},
])
def test_comparator_configuration_rejects_invalid_schedule(schedule: dict) -> None:
    configuration = CaseConfiguration(layer31_tr_reference_schedule=schedule)
    with pytest.raises(ValueError):
        configuration.validate()
