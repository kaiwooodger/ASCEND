from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from ascend.reporting.export import export_case
from ascend.validation.eclipse_harness.schemas import AcceptanceCriteria
from ascend.validation.provenance import base_provenance, git_commit

from .helpers import synthetic_case


ROOT = Path(__file__).resolve().parents[1]
TEST_COMMIT = "0123456789abcdef0123456789abcdef01234567"


class RepositoryReleaseContractTests(unittest.TestCase):
    def test_export_schema_and_research_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ASCEND_GIT_COMMIT": TEST_COMMIT}
        ):
            root = Path(directory)
            case = synthetic_case(root / "case")
            case.configuration.layer31_mlq_tumour_parameters = {"parameter_set_id": "tumour-v1"}
            case.configuration.layer31_tcp_parameters = {"parameter_set_id": "tcp-v1"}
            output = export_case(case, root / "exports")
            payload = json.loads(next(path for path in output if path.name == "ascend_result.json").read_text())
            schema = json.loads((ROOT / "validation" / "validation_schema.json").read_text())
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
            self.assertEqual(payload["provenance"]["ascend_version"], "1.3.5")
            self.assertEqual(payload["provenance"]["git_commit"], TEST_COMMIT)
            self.assertEqual(payload["provenance"]["parameter_set_ids"], ["tcp-v1", "tumour-v1"])

    def test_shared_provenance_uses_release_commit_environment(self) -> None:
        with patch.dict("os.environ", {"ASCEND_GIT_COMMIT": TEST_COMMIT}):
            self.assertEqual(git_commit(), TEST_COMMIT)
            provenance = base_provenance("configuration-hash", "layer1-run")
        self.assertEqual(provenance["git_commit"], TEST_COMMIT)
        self.assertEqual(provenance["configuration_hash"], "configuration-hash")

    def test_validation_criteria_are_frozen_and_match_runtime_reference(self) -> None:
        frozen = json.loads((ROOT / "validation" / "acceptance_criteria.json").read_text())
        runtime = json.loads((ROOT / "configs" / "eclipse_dvh_acceptance_v1.json").read_text())
        self.assertEqual(frozen["status"], "prospectively_frozen_before_retrospective_cohort")
        self.assertEqual(frozen["dose_endpoints"], runtime["dose_endpoints"])
        self.assertEqual(
            frozen["percentage_volume_endpoints"],
            runtime["percentage_volume_endpoints"],
        )
        self.assertEqual(frozen["structure_volume"], runtime["structure_volume"])
        resolved = AcceptanceCriteria.from_dict(frozen)
        self.assertEqual(resolved.version, runtime["version"])
        self.assertEqual(resolved.dose_absolute_floor_gy, runtime["dose_endpoints"]["absolute_floor_gy"])

    def test_repository_governance_files_are_present(self) -> None:
        required = [
            ".github/workflows/tests.yml", ".github/workflows/formal-validation.yml",
            ".github/workflows/release.yml", ".github/pull_request_template.md",
            ".devcontainer/devcontainer.json", "CHANGELOG.md", "CITATION.cff", "LICENSE",
            "SECURITY.md", "validation/validation_protocol.md",
        ]
        self.assertFalse([path for path in required if not (ROOT / path).is_file()])

    def test_release_checksums_exclude_and_verify_the_manifest(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        self.assertIn("sha256sum ./*.whl ./*.tar.gz > SHA256SUMS.txt", workflow)
        self.assertIn("sha256sum --check SHA256SUMS.txt", workflow)
        self.assertNotIn("sha256sum ./* > SHA256SUMS.txt", workflow)


if __name__ == "__main__":
    unittest.main()
