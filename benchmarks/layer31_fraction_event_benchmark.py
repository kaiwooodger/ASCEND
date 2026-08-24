"""Reproducible non-clinical Layer 3.1 orchestration micro-benchmark."""

from __future__ import annotations

import json
from pathlib import Path
import resource
import sys
import tempfile
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ascend.layer3.lq.service import Layer31Service
from ascend.validation.provenance import canonical_hash
from tests.helpers import synthetic_case


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        case = synthetic_case(Path(folder))
        item = case.layer1.result["manifest"]["roi_inventory"][0]
        case.configuration.layer31_roi_parameters = [{
            "roi_identity": item["roi_identity"], "alpha_beta_gy": 10.0,
            "parameter_source": "benchmark", "parameter_source_type": "configured_reference",
            "parameter_set_version": "benchmark-v1", "assignment_method": "identity_bound",
        }]
        case.configuration.layer31_mlq_tumour_parameters = {
            "parameter_set_id": "benchmark-tumour", "parameter_source": "benchmark",
            "model_source": "synthetic benchmark", "alpha_per_gy": 0.3,
            "beta_per_gy2": 0.03, "delta_per_gy": 0.02, "repair_half_time": 0.5,
            "treatment_delivery_time": 0.2, "time_unit": "hours",
        }
        case.configuration.layer31_mlq_normal_parameters = {
            **case.configuration.layer31_mlq_tumour_parameters,
            "parameter_set_id": "benchmark-normal",
        }
        case.configuration_hash = canonical_hash(case.configuration.to_dict())
        service = Layer31Service(); records = []
        for index in range(6):
            started = time.perf_counter(); result = service.run(case); elapsed = time.perf_counter() - started
            records.append({
                "run": index, "phase": "preparation" if index == 0 else "warm",
                "wall_seconds": elapsed, "calculation_status": result.calculation_status,
            })
        warm = sorted(item["wall_seconds"] for item in records[1:])
        print(json.dumps({
            "profile": "synthetic_21x21x21_four_vertices",
            "voxel_count": 21 ** 3,
            "preparation_wall_seconds": records[0]["wall_seconds"],
            "warm_median_wall_seconds": warm[len(warm) // 2],
            "peak_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "records": records,
            "scope": "micro-benchmark_only_not_clinical_export_benchmark",
        }, indent=2))


if __name__ == "__main__":
    main()
