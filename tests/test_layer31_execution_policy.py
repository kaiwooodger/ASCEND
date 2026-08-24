from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch

import numpy as np

from ascend.layer3.lq.service import Layer31Service
from ascend.validation.provenance import canonical_hash

from .helpers import synthetic_case


def _assign(case, *, source_type: str = "configured_reference") -> None:
    item = case.layer1.result["manifest"]["roi_inventory"][0]
    case.configuration.layer31_roi_parameters = [{
        "roi_identity": item["roi_identity"],
        "alpha_beta_gy": 10.0,
        "parameter_source": "synthetic policy test",
        "parameter_source_type": source_type,
        "parameter_set_version": "policy-v1",
        "assignment_method": "identity_bound_test",
    }]
    case.configuration_hash = canonical_hash(case.configuration.to_dict())


def test_disabled_sensitivity_matrix_is_not_called() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _assign(case)
        case.configuration.layer31_sensitivity_sweep_enabled = False
        with patch(
            "ascend.layer3.lq.service.run_sensitivity_scenario_matrix",
            side_effect=AssertionError("disabled sensitivity calculation was called"),
        ):
            result = Layer31Service().run(case).result["layer3_1c_sensitivity_scenario_matrix"]
        assert result == {
            "status": "NOT_ASSESSED", "calculation_status": "not_run",
            "applicability_status": "NOT_ASSESSED",
            "reason": "SENSITIVITY_SWEEP_DISABLED", "records": [], "enabled": False,
        }


def test_layer31a_merge_preserves_manual_parameter_warning_and_provisional_state() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _assign(case, source_type="user_selected")
        branch = Layer31Service().run(case).result["layer3_1a_conventional_lq"]
        assert branch["status"] == "WARN"
        assert branch["calculation_status"] == "completed_with_warnings"
        assert branch["interpretation_status"] == "provisional"
        assert "manual_radiobiological_parameter" in branch["warnings"]


def test_summary_run_defers_large_fields_and_viewer_materialisation_omits_duplicates() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder)); _assign(case)
        service = Layer31Service()
        case.layer3_1 = service.run(case)
        artifact = case.layer3_1.result["layer3_1a_conventional_lq"]["artifacts"]
        assert artifact["materialisation_status"] == "not_materialised"
        assert "spatial_fields_path" not in artifact

        service.materialise_visualisation_fields(case)
        branch = case.layer3_1.result["layer3_1a_conventional_lq"]
        artifact = branch["artifacts"]
        assert artifact["materialisation_status"] == "materialised_on_request"
        with np.load(artifact["spatial_fields_path"], allow_pickle=False) as archive:
            assert "physical_course_dose_gy" not in archive.files
            assert "LQ_high_dose_warning_mask" not in archive.files
            assert any(key.startswith("spatial_BED_LQ") for key in archive.files)
        warning = branch["high_dose_warning"]
        assert warning["configured"] is False
        assert warning["array_key"] is None
        assert warning["flagged_voxel_count"] is None


def test_service_basis_is_constructed_directly_from_shared_fraction_history() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder))
        result, _components, history = Layer31Service().build_basis_with_history(case)
        assert result.basis is not None and history.history is not None
        basis = result.basis
        assert basis.algorithm_version == "ASCEND-L3.1-fraction-event-PQ-v2.0"
        assert basis.provenance["authoritative_accumulation"] == "shared_fraction_event_engine"
        assert basis.provenance["fraction_history_hash"] == history.history.history_hash
        expected_p = np.add.reduce([
            np.asarray(event.combined_fraction_dose_field, dtype=np.float32)
            for event in history.history.events
        ])
        expected_q = np.add.reduce([
            np.square(np.asarray(event.combined_fraction_dose_field, dtype=np.float32), dtype=np.float32)
            for event in history.history.events
        ])
        assert np.array_equal(basis.p_map, expected_p)
        assert np.array_equal(basis.q_map, expected_q)
