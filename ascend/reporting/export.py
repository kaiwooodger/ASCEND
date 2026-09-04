"""Case-level JSON and CSV export adapters over already stored results."""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ascend.models.case import ASCENDCase
from ascend.validation.provenance import software_identity
from ascend.workflow.preferences import normalise_vertex_records, selected_supporting_outputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _parameter_set_ids(case: ASCENDCase) -> list[str]:
    """Collect configured biological parameter identities without interpreting them."""
    candidates = [
        case.configuration.layer31_mlq_tumour_parameters.get("parameter_set_id"),
        case.configuration.layer31_mlq_normal_parameters.get("parameter_set_id"),
        case.configuration.layer31_tcp_parameters.get("parameter_set_id"),
    ]
    candidates.extend(
        item.get("parameter_set_version") or item.get("parameter_set_id")
        for item in case.configuration.layer31_roi_parameters
    )
    return sorted({str(item).strip() for item in candidates if str(item or "").strip()})


def export_case(case: ASCENDCase, destination: str | Path) -> list[Path]:
    """Render files from existing structured results. No metric is recalculated."""
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ASCEND-case-result-v1",
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            **software_identity(),
            "configuration_hash": case.configuration_hash,
            "parameter_set_ids": _parameter_set_ids(case),
        },
        "case": case.to_dict(include_results=True),
    }
    json_path = output / "ascend_result.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    created = [json_path]
    configuration_path = output / "ascend_case_config.json"
    configuration_path.write_text(json.dumps(case.configuration.to_dict(), indent=2), encoding="utf-8")
    created.append(configuration_path)
    summary = [{
        "case_id": case.case_id, "configuration_hash": case.configuration_hash,
        "layer1_status": case.layer1_status,
        "layer2_1_calculation_status": case.layer2_1.calculation_status,
        "layer2_1_interpretation_status": case.layer2_1.interpretation_status,
        "layer2_2_calculation_status": case.layer2_2.calculation_status,
        "layer2_2_interpretation_status": case.layer2_2.interpretation_status,
        "layer3_1_status": case.layer3_1.calculation_status,
        "layer3_2_status": case.layer3_2.calculation_status if case.configuration.layer32_enabled else "not_assessed_disabled",
    }]
    summary_path = output / "ascend_summary.csv"
    _write_csv(summary_path, summary)
    created.append(summary_path)
    if case.layer2_1.result:
        path = output / "layer2_1_metrics.csv"
        _write_csv(path, case.layer2_1.result.get("harmonised_metrics", []))
        if path.exists(): created.append(path)
        supporting = selected_supporting_outputs(
            case.layer2_1.result.get("supporting_outputs", {}),
            case.configuration.supporting_outputs_enabled,
            case.configuration.supporting_output_categories,
        )
        if supporting:
            supporting_path = output / "layer2_1_supporting_outputs.json"
            supporting_path.write_text(json.dumps(supporting, indent=2), encoding="utf-8")
            created.append(supporting_path)
        if "per_vertex_qa" in supporting:
            path = output / "layer2_1_per_vertex_qa.csv"
            _write_csv(path, normalise_vertex_records(supporting.get("per_vertex_qa", [])))
            if path.exists(): created.append(path)
        if "oar_vertex_geometry" in supporting:
            path = output / "layer2_1_oar_vertex_geometry.csv"
            _write_csv(path, supporting.get("oar_vertex_geometry", {}).get("records", []))
            if path.exists(): created.append(path)
        if "protocol_native_metrics" in supporting:
            path = output / "layer2_1_protocol_native_endpoints.csv"
            configured = {item["id"]: item for item in case.configuration.protocol_native_endpoints}
            rows = [{**configured.get(str(item.get("id")), {}), **item} for item in supporting["protocol_native_metrics"]]
            _write_csv(path, rows)
            if path.exists(): created.append(path)
    eclipse_import = (case.layer1.result or {}).get("eclipse_dvh_import", {})
    eclipse_audit = (case.layer1.result or {}).get("eclipse_dvh_audit", [])
    if eclipse_import or eclipse_audit:
        path = output / "eclipse_dvh_supplied_reference_metrics.csv"
        _write_csv(path, eclipse_import.get("metrics", []))
        if path.exists(): created.append(path)
        path = output / "eclipse_dvh_supplied_reference_audit.csv"
        _write_csv(path, eclipse_audit)
        if path.exists(): created.append(path)
        path = output / "eclipse_dvh_supplied_reference_manifest.json"
        path.write_text(json.dumps(eclipse_import, indent=2), encoding="utf-8")
        created.append(path)
    supplied_records = case.configuration.eclipse_endpoint_prefill.get("supplied_records", [])
    if supplied_records:
        path = output / "eclipse_dvh_configured_reference_records.csv"
        _write_csv(path, supplied_records)
        if path.exists(): created.append(path)
    if case.layer2_2.result:
        path = output / "layer2_2_edges.csv"
        _write_csv(path, case.layer2_2.result.get("edges", []))
        if path.exists(): created.append(path)
        path = output / "layer2_2_nodes.csv"
        _write_csv(path, case.layer2_2.result.get("nodes", []))
        if path.exists(): created.append(path)
        from ascend.layer2.graph.exports import export_layer22_extensions
        created.extend(export_layer22_extensions(case.layer2_2.result, output))
    if case.layer3_1.result:
        from ascend.layer3.lq.service import Layer31Service
        created.extend(Layer31Service().export(case, output / "layer3_1"))
    if case.configuration.layer32_enabled and case.layer3_2.result:
        layer32_json = output / "layer3_2_nonlocal_effect_results.json"
        layer32_json.write_text(json.dumps(case.layer3_2.result, indent=2), encoding="utf-8")
        created.append(layer32_json)
        path = output / "layer3_2_graph_edge_metrics.csv"
        _write_csv(path, case.layer3_2.result.get("edge_metrics", []))
        if path.exists(): created.append(path)
        path = output / "layer3_2_peri_gtv_spill_shells.csv"
        _write_csv(path, case.layer3_2.result.get("peri_gtv_spill_shells", []))
        if path.exists(): created.append(path)
        path = output / "layer3_2_oar_biological_spill.csv"
        _write_csv(path, case.layer3_2.result.get("oar_biological_spill", []))
        if path.exists(): created.append(path)
        field_source = Path(str(case.layer3_2.result.get("artifacts", {}).get("fields_path") or ""))
        if field_source.is_file():
            field_output = output / "layer3_2_fields.npz"
            shutil.copy2(field_source, field_output)
            created.append(field_output)
    return created
