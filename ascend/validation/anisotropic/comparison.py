"""Independent comparison calculations for validation evidence."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ascend.dicom.geometry import normalise_rtdose_geometry
from ascend.scientific.legacy import layer21_validated

from .analytic_dose import dose_statistics, physical_gradient_field, uniform_field
from .analytic_geometry import GridSpec, contour_stack_volume_cc, relative_error_pct, sampled_volume_cc
from .fixtures import build_fixture, write_dicom_fixture


ANISOTROPIC_GRIDS = (
    GridSpec("reference_1x1x1", (1.0, 1.0, 1.0)),
    GridSpec("A_1x1x2", (1.0, 1.0, 2.0)),
    GridSpec("B_1x1x2.5", (1.0, 1.0, 2.5)),
    GridSpec("C_1x2x2.5", (1.0, 2.0, 2.5)),
    GridSpec("D_2x2x3", (2.0, 2.0, 3.0)),
)


def _area_functions() -> dict[str, Callable[[float], float]]:
    def sphere(radius: float, multiplier: float = 1.0) -> Callable[[float], float]:
        return lambda z: multiplier * math.pi * max(0.0, radius ** 2 - z ** 2)

    return {
        "GTV": sphere(10.0),
        "PTVLOW": lambda z: 60.0 if abs(z) <= 5.0 else 0.0,
        "VTVH": sphere(3.0, 4.0),
        "VTVL": lambda z: 9.0 if abs(z) <= 2.0 else 0.0,
    }


def _volume_records(grid: GridSpec, masks: dict[str, np.ndarray], analytic: dict[str, float]) -> list[dict[str, Any]]:
    areas = _area_functions()
    records: list[dict[str, Any]] = []
    for name in ("GTV", "PTVLOW", "VTVH", "VTVL"):
        contour = contour_stack_volume_cc(grid, areas[name])
        sampled = sampled_volume_cc(masks[name], grid)
        records.append({
            "structure": name,
            "analytic_volume_cc": analytic[name],
            "contour_volume_cc": contour,
            "ct_volume_cc": sampled,
            "dose_sampled_volume_cc": sampled,
            "ct_voxel_count": int(masks[name].sum()),
            "dose_voxel_count": int(masks[name].sum()),
            "contour_error_pct": relative_error_pct(contour, analytic[name]),
            "ct_error_pct": relative_error_pct(sampled, analytic[name]),
            "dose_error_pct": relative_error_pct(sampled, analytic[name]),
        })
    return records


def _layer21_result(
    grid: GridSpec,
    fixture: Any,
    volume_records: list[dict[str, Any]],
) -> dict[str, Any]:
    volumes = {item["structure"]: item for item in volume_records}
    layer1 = {
        "manifest": {
            "rtdose_uid": f"synthetic-{grid.name}",
            "treatment_component": "LRT_VALIDATION",
            "dose_grid": {"voxel_spacing_mm": list(grid.spacing_zyx_mm)},
            "rasterisation": {"volume_definitions": {
                name: {
                    "anatomical_volume_contour_cc": item["contour_volume_cc"],
                    "anatomical_volume_ct_cc": item["ct_volume_cc"],
                    "dose_sampled_volume_cc": item["dose_sampled_volume_cc"],
                }
                for name, item in volumes.items()
            }},
            "mask_export": {"sha256": "synthetic-validation"},
        },
        "findings": [],
    }
    with tempfile.TemporaryDirectory(prefix="ascend-anisotropic-") as folder:
        root = Path(folder)
        (root / "layer1_result.json").write_text(json.dumps(layer1), encoding="utf-8")
        config = {
            "_layer1_dir": root,
            "roles": {
                "GTV": "GTV", "T_L": "PTVLOW", "VTV_H": "VTVH", "VTV_L": "VTVL",
            },
            "prescriptions": {
                "Rx_L": {"gy": 10.0, "source": "protocol_configuration"},
                "Rx_H": {"gy": 20.0, "source": "protocol_configuration"},
            },
            "dose_context": {
                "treatment_component": "LRT_VALIDATION",
                "dose_object_uid": f"synthetic-{grid.name}",
                "protocol_confirmed": True,
            },
            "protocol_context": {
                "prescriptions_confirmed": True,
                "roles_confirmed": True,
                "dose_object_confirmed": True,
                "valley_confirmed": True,
            },
            "valley_definition_source": "synthetic analytic valley",
        }
        payload = layer21_validated.analyse(layer1, fixture.dose_gy, fixture.masks, config)
    metrics = {item["metric_id"]: item for item in payload["harmonised_metrics"]}
    expected = {
        "peripheral_coverage_v95_rxl": 100.0,
        "high_dose_coverage_v95_rxh": 100.0,
        "high_dose_volume_fraction": 100.0 * volumes["VTVH"]["contour_volume_cc"] / volumes["GTV"]["contour_volume_cc"],
        "mean_peak_dose": 20.0,
        "mean_valley_dose": 5.0,
        "structure_based_dose_ratio": 4.0,
    }
    comparisons = []
    for metric_id in layer21_validated.PRIMARY_IDS:
        actual = metrics[metric_id].get("value")
        tolerance = 1.0e-6
        comparisons.append({
            "metric_id": metric_id,
            "expected": expected[metric_id],
            "actual": actual,
            "absolute_error": abs(float(actual) - expected[metric_id]) if actual is not None else None,
            "status": "PASS" if actual is not None and abs(float(actual) - expected[metric_id]) <= tolerance else "FAIL",
            "applicability": metrics[metric_id].get("applicability"),
        })
    return {
        "status": "PASS" if all(item["status"] == "PASS" for item in comparisons) else "FAIL",
        "metrics": comparisons,
    }


def validate_grid(grid: GridSpec) -> dict[str, Any]:
    """Validate grid and raise a controlled error when requirements are not met."""
    fixture = build_fixture(grid)
    geometry = normalise_rtdose_geometry(fixture.rtdose)
    probe = tuple(int(value // 2) for value in grid.shape_zyx)
    manual = grid.physical_point(probe)
    z, y, x = probe
    reconstructed = (
        geometry["origin"]
        + x * geometry["spacing"][1] * geometry["row_dir"]
        + y * geometry["spacing"][0] * geometry["col_dir"]
        + geometry["offsets"][z] * geometry["normal"]
    )
    uniform_stats = dose_statistics(uniform_field(grid), fixture.masks["GTV"])
    gradient = physical_gradient_field(grid, 5.0, (0.1, 0.02, 0.03))
    expected_probe_dose = 5.0 + 0.1 * manual[0] + 0.02 * manual[1] + 0.03 * manual[2]
    volumes = _volume_records(grid, fixture.masks, fixture.analytic_volumes_cc)
    layer21 = _layer21_result(grid, fixture, volumes)
    geometry_pass = bool(np.allclose(manual, reconstructed, rtol=0.0, atol=1.0e-9))
    uniform_pass = all(abs(value - 10.0) <= 1.0e-12 for key, value in uniform_stats.items() if key != "dmax_gy" or True)
    gradient_pass = abs(float(gradient[probe]) - expected_probe_dose) <= 1.0e-12
    anisotropic = len({round(value, 9) for value in grid.spacing_zyx_mm}) > 1
    layer22 = {
        "calculation_status": "outside_validated_scope" if anisotropic else "completed",
        "reason": "anisotropic_grid_outside_validated_scope" if anisotropic else None,
    }
    return {
        "grid_id": grid.name,
        "spacing_xyz_mm": list(grid.spacing_xyz_mm),
        "spacing_zyx_mm": list(grid.spacing_zyx_mm),
        "shape_zyx": list(grid.shape_zyx),
        "geometry": {
            "status": "PASS" if geometry_pass else "FAIL",
            "uniform_frame_spacing": geometry["uniform_frame_spacing"],
            "isotropic": geometry["isotropic"],
            "anisotropic": geometry["anisotropic"],
            "probe_index_zyx": list(probe),
            "manual_patient_coordinate_mm": manual.tolist(),
            "ascend_patient_coordinate_mm": reconstructed.tolist(),
        },
        "volume_validation": volumes,
        "uniform_dose_validation": {"status": "PASS" if uniform_pass else "FAIL", **uniform_stats},
        "physical_gradient_validation": {
            "status": "PASS" if gradient_pass else "FAIL",
            "expected_probe_dose_gy": expected_probe_dose,
            "actual_probe_dose_gy": float(gradient[probe]),
        },
        "layer1": {"calculation_status": "completed" if geometry_pass and uniform_pass and gradient_pass else "blocked"},
        "layer2_1": layer21,
        "layer2_2": layer22,
    }


def validate_complete_dicom_pipeline(grid: GridSpec) -> dict[str, Any]:
    """Exercise the public controller on a complete synthetic classic DICOM-RT chain."""
    from ascend.app.controller import ApplicationController
    from ascend.models.config import CaseConfiguration

    with tempfile.TemporaryDirectory(prefix="ascend-anisotropic-dicom-") as folder:
        root = Path(folder)
        source = write_dicom_fixture(root / "dicom", grid)
        controller = ApplicationController()
        case = controller.import_case(source, root / "case")
        configuration = CaseConfiguration.from_dict(json.loads((source / "validation_config.json").read_text(encoding="utf-8")))
        controller.configure(configuration)
        layer1 = controller.run_layer1()
        layer21 = controller.run_layer21()
        layer22 = controller.run_layer22()
        metrics = {
            item["metric_id"]: {
                "value": item.get("value"),
                "applicability": item.get("applicability"),
                "warnings": item.get("warnings", []),
            }
            for item in (layer21.result or {}).get("harmonised_metrics", [])
        }
        anisotropic = len({round(value, 9) for value in grid.spacing_zyx_mm}) > 1
        expected_layer22 = "outside_validated_scope" if anisotropic else {
            "completed", "completed_with_warnings",
        }
        layer22_pass = (
            layer22.calculation_status == expected_layer22
            if isinstance(expected_layer22, str)
            else layer22.calculation_status in expected_layer22
        )
        passed = (
            layer1.calculation_status in {"completed", "completed_with_warnings"}
            and layer21.calculation_status in {"completed", "completed_with_warnings"}
            and layer22_pass
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "layer1_calculation_status": layer1.calculation_status,
            "layer1_error": layer1.error,
            "layer1_validation_status": case.layer1_status,
            "layer2_1_calculation_status": layer21.calculation_status,
            "layer2_1_error": layer21.error,
            "layer2_2_calculation_status": layer22.calculation_status,
            "layer2_2_error": layer22.error,
            "layer2_2_expected_status": (
                expected_layer22 if isinstance(expected_layer22, str) else sorted(expected_layer22)
            ),
            "layer2_1_metrics": metrics,
        }


def run_anisotropic_validation(
    grids: tuple[GridSpec, ...] = ANISOTROPIC_GRIDS,
    include_complete_dicom_pipeline: bool = False,
) -> dict[str, Any]:
    """Execute anisotropic validation and return its explicit calculation state and evidence."""
    results = [validate_grid(grid) for grid in grids]
    if include_complete_dicom_pipeline:
        for grid, item in zip(grids, results):
            item["complete_dicom_pipeline"] = validate_complete_dicom_pipeline(grid)
    passed = all(
        item["geometry"]["status"] == "PASS"
        and item["uniform_dose_validation"]["status"] == "PASS"
        and item["physical_gradient_validation"]["status"] == "PASS"
        and item["layer2_1"]["status"] == "PASS"
        and item.get("complete_dicom_pipeline", {}).get("status", "PASS") == "PASS"
        for item in results
    )
    return {
        "schema_version": "ASCEND-anisotropic-validation-v1",
        "scope": "Regular RTDOSE grids only; validation harness outside locked scientific kernels.",
        "status": "PASS" if passed else "FAIL",
        "tested_resolution_domain": [list(grid.spacing_xyz_mm) for grid in grids],
        "results": results,
    }
