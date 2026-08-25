"""Generate deterministic ASCEND metrics for the cross-platform CI gate."""

from __future__ import annotations

import argparse
import json
import os
import platform
import tempfile
from pathlib import Path

import numpy as np
import PySide6
import pyvista
import scipy
import vtk
from PySide6.QtCore import QLibraryInfo

import ascend
from ascend.layer2.graph.service import Layer22Service
from ascend.layer2.metrics.service import Layer21Service
from ascend.layer3.lq.metrics import bed_values, eqd2_values_from_bed
from ascend.layer3.lq.service import Layer31Service
from ascend.validation.provenance import canonical_hash

from .helpers import synthetic_case


def _mlq_parameter_set(identifier: str) -> dict[str, object]:
    return {
        "parameter_set_id": identifier,
        "parameter_source": "ASCEND synthetic cross-platform reference",
        "model_source": "specified synthetic reference equation",
        "alpha_per_gy": 0.3,
        "beta_per_gy2": 0.03,
        "delta_per_gy": 0.02,
        "repair_half_time": 0.5,
        "treatment_delivery_time": 0.2,
        "time_unit": "hours",
    }


def generate_report() -> dict[str, object]:
    """Run one non-clinical reference case through physical and biological layers."""
    with tempfile.TemporaryDirectory(prefix="ascend-cross-platform-") as directory:
        case = synthetic_case(Path(directory), explicit_vertices=True)
        case.layer2_1 = Layer21Service().run(case)
        case.layer2_2 = Layer22Service().run(case)

        case.configuration.layer31_mlq_tumour_parameters = _mlq_parameter_set("tumour-ci-v1")
        case.configuration.layer31_mlq_normal_parameters = _mlq_parameter_set("normal-ci-v1")
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        case.layer3_1 = Layer31Service().run(case)

        with np.load(case.layer1.result["manifest"]["mask_export"]["path"], allow_pickle=False) as archive:
            dose = np.asarray(archive["dose_gy"], dtype=np.float64)
            gtv = np.asarray(archive["GTV"], dtype=bool)
            valley = np.asarray(archive["VTVL"], dtype=bool)

        selected_dose = dose[gtv]
        sbed = bed_values(selected_dose, np.square(selected_dose), 10.0)
        seqd2 = eqd2_values_from_bed(sbed, 10.0)
        physical = {item["metric_id"]: item for item in case.layer2_1.result["harmonised_metrics"]}
        response = case.layer3_1.result["layer3_1b_high_dose_sfrt_response"]
        therapeutic_ratio = case.layer3_1.result["layer3_1c_modelled_therapeutic_ratio"]

        metrics = {
            "dose_d95_gy": float(np.percentile(selected_dose, 5.0)),
            "dose_d50_gy": float(np.percentile(selected_dose, 50.0)),
            "mean_dose_gy": float(np.mean(selected_dose)),
            "physical_ipvdr": float(case.layer2_2.result["plan_ipvdr"]["primary_median"]),
            "vertex_count": int(physical["high_dose_volume_fraction"]["number_of_vertices"]),
            "valley_volume_cc": float(np.count_nonzero(valley) / 1000.0),
            "voxel_volume_cc": 0.001,
            "sbed_d95_gy": float(np.percentile(sbed, 5.0)),
            "sbed_mean_gy": float(np.mean(sbed)),
            "seqd2_d95_gy": float(np.percentile(seqd2, 5.0)),
            "seqd2_mean_gy": float(np.mean(seqd2)),
            "mean_mlq_survival": float(response["mean_tumour_survival_fraction"]),
            "mlq_eud_gy": float(response["tumour_eud_gy"]),
            "modelled_therapeutic_ratio": float(therapeutic_ratio["modelled_therapeutic_ratio"]),
        }
        if not all(np.isfinite(value) for value in metrics.values()):
            raise RuntimeError("Cross-platform report contains a non-finite scientific value.")

        return {
            "schema_version": "ASCEND-cross-platform-report-v1",
            "case_id": "SYNTHETIC-CROSS-PLATFORM-V1",
            "ascend_version": ascend.__version__,
            "ascend_commit": os.environ.get("ASCEND_GIT_COMMIT", "local-uncommitted-worktree"),
            "environment": {
                "runner_os": os.environ.get("RUNNER_OS", platform.system()),
                "runner_arch": os.environ.get("RUNNER_ARCH", platform.machine()),
                "python_implementation": platform.python_implementation(),
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "platform_machine": platform.machine(),
                "qt_version": QLibraryInfo.version().toString(),
                "pyside6_version": PySide6.__version__,
                "numpy_version": np.__version__,
                "scipy_version": scipy.__version__,
                "pyvista_version": pyvista.__version__,
                "vtk_version": vtk.vtkVersion.GetVTKVersion(),
            },
            "reference_geometry": {
                "dose_shape_zyx": list(dose.shape),
                "gtv_voxel_count": int(np.count_nonzero(gtv)),
                "valley_voxel_count": int(np.count_nonzero(valley)),
                "voxel_spacing_mm": [1.0, 1.0, 1.0],
            },
            "metrics": metrics,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = generate_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
