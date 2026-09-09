"""CLI integration tests for automation completion and actionable input errors."""

import json

import pytest

from ascend.cli import main
from ascend.layer3.lq.service import Layer31Service
from ascend.layer3.lq.workflow_status import summarise_workflow
from ascend.models.config import CaseConfiguration
from benchmarks.generate_eclipse_fixture import generate
from tests.helpers import synthetic_case
from tests.test_layer31_lq import _assign_all_lrt_roles
from tests.test_layer31_response import parameter_set


def test_resume_blocked_physical_run_returns_nonzero_and_json(tmp_path, capsys):
    case = synthetic_case(tmp_path / "case")
    case.layer1.mark_stale("New RTDOSE was selected")
    case.layer1_status = "STALE"
    case.save()
    assert main(["resume", str(case.root), "--layer", "physical"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["layers"]["layer2_1"]["calculation_status"] == "blocked"


@pytest.mark.parametrize("command", ["discover", "run", "resume", "cache-inspect"])
def test_missing_inputs_are_actionable_json(tmp_path, capsys, command):
    arguments = [command, str(tmp_path / "missing")]
    if command == "run":
        arguments += ["--case-root", str(tmp_path / "case")]
    assert main(arguments) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["reason"]
    assert not (tmp_path / "case").exists()


def test_invalid_configuration_does_not_create_case(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    config = tmp_path / "invalid.json"
    config.write_text("[]")
    assert main(["run", str(source), "--case-root", str(tmp_path / "case"), "--config", str(config)]) == 2
    assert "JSON object" in json.loads(capsys.readouterr().out)["reason"]
    assert not (tmp_path / "case").exists()


def test_partial_biology_is_preserved_but_cli_reports_incomplete(tmp_path, capsys):
    case = synthetic_case(tmp_path / "case")
    _assign_all_lrt_roles(case)
    case.save()
    assert main(["layer31", "--case", str(case.root)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["workflow_status"] == "partial"
    assert any("3.1B" in reason for reason in result["blocking_reasons"])
    record = Layer31Service().run(case)
    assert record.result["roi_results"]
    case.configuration.layer31_mlq_tumour_parameters = parameter_set("audit-tumour")
    complete = Layer31Service().run(case)
    assert complete.result["workflow_status"] == "complete"


def test_requested_optional_branch_failure_is_part_of_completion_gate():
    configuration = CaseConfiguration()
    configuration.layer31_tcp_parameters = {"parameter_set_id": "incomplete"}
    payload = {
        "calculation_status": "completed",
        "layer3_1a_conventional_lq": {"status": "PASS"},
        "layer3_1b_high_dose_sfrt_response": {"status": "PASS"},
        "layer3_1d_tumour_control_probability": {"status": "BLOCKED", "reason": "PARAMETERS_MISSING"},
    }
    summarise_workflow(payload, configuration)
    assert payload["workflow_status"] == "partial"
    assert payload["blocking_reasons"] == ["3.1D: PARAMETERS_MISSING"]


def test_dicom_cli_run_and_resume_export(tmp_path, capsys):
    source = generate(tmp_path / "source", 24, 24, 8, 8, 8)
    case_root = tmp_path / "case"
    assert main([
        "run", str(source), "--case-root", str(case_root),
        "--config", str(source / "benchmark_config.json"), "--layer1-only",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["layer1"] in {"PASS", "WARN"}
    assert main(["resume", str(case_root), "--layer", "layer2_1"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["layers"]["layer2_1"]["calculation_status"] in {"completed", "completed_with_warnings"}
    assert main(["resume", str(case_root), "--layer", "export"]) == 0
    assert (case_root / "exports" / "ascend_result.json").is_file()
