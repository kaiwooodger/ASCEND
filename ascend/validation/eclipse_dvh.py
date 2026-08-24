"""Parsing, normalization, and Layer 1 comparison of Eclipse DVH text exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any


SUPPORTED_METRICS = ("Volume", "D95", "D2", "Dmin", "Dmax", "Dmean")
ROLE_CANONICAL = {
    "GTV": "GTV",
    "T_L": "T_L",
    "peripheral_target": "T_L",
    "VTV_H": "VTV_H",
    "high_dose_target": "VTV_H",
    "VTV_L": "VTV_L",
    "planned_valley": "VTV_L",
}


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric(value: str) -> float | None:
    cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace("'", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _field(lines: list[str], label: str) -> str | None:
    wanted = _norm(label)
    for line in lines:
        match = re.match(r"^(.+?):\s*(.*?)\s*$", line)
        if match and _norm(re.sub(r"\[[^\]]+\]", "", match.group(1))) == wanted:
            return match.group(2) or None
    return None


def _field_with_unit(lines: list[str], labels: tuple[str, ...]) -> tuple[str | None, str]:
    wanted = {_norm(label) for label in labels}
    for line in lines:
        match = re.match(r"^(.+?)(?:\s*\[([^\]]+)\])?\s*:\s*(.*?)\s*$", line)
        if match and _norm(match.group(1)) in wanted:
            return match.group(3) or None, (match.group(2) or "").strip()
    return None, ""


def _metric_name(label: str) -> str | None:
    value = _norm(label)
    if value in {"VOLUME", "STRUCTUREVOLUME", "TOTALVOLUME"}:
        return "Volume"
    if value in {"MINDOSE", "MINIMUMDOSE"}:
        return "Dmin"
    if value in {"MAXDOSE", "MAXIMUMDOSE"}:
        return "Dmax"
    if value in {"MEANDOSE", "AVERAGEDOSE"}:
        return "Dmean"
    dose_match = re.fullmatch(r"D\s*(\d+(?:[.,]\d+)?)\s*%?", label.strip(), re.IGNORECASE)
    if dose_match:
        percentage = float(dose_match.group(1).replace(",", "."))
        if 0 <= percentage <= 100:
            return f"D{percentage:g}"
    relative_volume_match = re.fullmatch(
        r"V\s*(\d+(?:[.,]\d+)?)\s*%?\s*Rx", label.strip(), re.IGNORECASE,
    )
    if relative_volume_match:
        percentage = float(relative_volume_match.group(1).replace(",", "."))
        if percentage > 0:
            return f"V{percentage:g}%Rx"
    absolute_volume_match = re.fullmatch(
        r"V\s*(\d+(?:[.,]\d+)?)\s*Gy", label.strip(), re.IGNORECASE,
    )
    if absolute_volume_match:
        dose_gy = float(absolute_volume_match.group(1).replace(",", "."))
        if dose_gy > 0:
            return f"V{dose_gy:g}Gy"
    return None


def _convert_value(metric: str, value: float, unit: str, total_dose_gy: float | None) -> tuple[float, str, str] | None:
    normalized = _norm(unit.replace("³", "3"))
    if metric == "Volume":
        if normalized in {"CM3", "CC", "ML"}:
            return value, "cc", "direct_volume"
        return None
    if metric.startswith("V"):
        if unit.strip() == "%" or normalized in {"PERCENT", "PCT"}:
            return value, "%", "direct_percentage"
        return None
    if normalized == "GY":
        return value, "Gy", "direct_gy"
    if normalized == "CGY":
        return value / 100.0, "Gy", "cgy_to_gy"
    if (unit.strip() == "%" or normalized == "PERCENT") and total_dose_gy and total_dose_gy > 0:
        return value * total_dose_gy / 100.0, "Gy", "relative_percent_of_total_dose"
    return None


@dataclass
class ParsedEclipseFile:
    """Represent parsed eclipse file state and behavior."""
    path: Path
    patient_id: str
    plan: str
    course: str
    total_dose_gy: float | None
    metrics: list[dict[str, Any]]
    curves: list[dict[str, Any]]
    issues: list[dict[str, str]]


def _read_text(source: Path) -> str:
    raw = source.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    sample = raw[:512]
    if sample and sample.count(b"\x00") > len(sample) // 4:
        even_nuls = sample[::2].count(b"\x00")
        odd_nuls = sample[1::2].count(b"\x00")
        return raw.decode("utf-16-be" if even_nuls > odd_nuls else "utf-16-le")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError(f"ECLIPSE_DVH_ENCODING: unsupported text encoding in {source.name}")


def _header_value(header: list[str], labels: tuple[str, ...]) -> str:
    for label in labels:
        value = _field(header, label)
        if value:
            return value
    return ""


def _is_curve_header(line: str) -> bool:
    return bool(
        re.search(r"(?i)\bdose\s*\[[^\]]+\]", line)
        and re.search(r"(?i)(ratio\s+of\s+total\s+structure\s+volume|relative\s+volume|volume)\s*\[%\]", line)
    )


def parse_eclipse_dvh_file(path: str | Path) -> ParsedEclipseFile:
    """Parse eclipse dvh file using the documented input contract."""
    source = Path(path)
    text = _read_text(source)
    structure_matches = list(re.finditer(r"(?m)^Structure:\s*(.*?)\s*$", text))
    if not structure_matches:
        raise ValueError(f"ECLIPSE_DVH_FORMAT: no Structure sections found in {source.name}")
    header = text[:structure_matches[0].start()].splitlines()
    patient_id = _header_value(header, ("Patient ID", "PatientID", "Patient Identifier"))
    plan = _header_value(header, ("Plan", "Plan ID", "Plan Name"))
    course = _header_value(header, ("Course", "Course ID", "Course Name"))
    total_text, total_unit = _field_with_unit(
        header,
        ("Total dose", "Total prescribed dose", "Normalization dose", "Normalisation dose"),
    )
    total_dose = _numeric(total_text or "")
    if total_dose is not None and _norm(total_unit) == "CGY":
        total_dose /= 100.0
    if not patient_id or not plan:
        raise ValueError(f"ECLIPSE_DVH_FORMAT: patient or plan is missing in {source.name}")
    if total_dose is not None and total_dose <= 0:
        raise ValueError(f"ECLIPSE_DVH_FORMAT: Total dose must be positive in {source.name}")

    metrics: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for index, match in enumerate(structure_matches):
        end = structure_matches[index + 1].start() if index + 1 < len(structure_matches) else len(text)
        section = text[match.end():end].splitlines()
        structure = match.group(1).strip()
        curve_index = next((i for i, line in enumerate(section) if _is_curve_header(line)), None)
        summary_lines = section if curve_index is None else section[:curve_index]
        for line in summary_lines:
            field_match = re.match(r"^(.+?)(?:\s*\[([^\]]+)\])?:\s*(.*?)\s*$", line)
            if not field_match:
                continue
            metric = _metric_name(field_match.group(1).strip())
            raw_value = _numeric(field_match.group(3))
            source_unit = (field_match.group(2) or "").strip()
            if not metric or raw_value is None:
                continue
            converted = _convert_value(metric, raw_value, source_unit, total_dose)
            if converted is None:
                if source_unit.strip() == "%" and total_dose is None:
                    issues.append({
                        "severity": "WARN",
                        "code": "relative_metric_without_normalization",
                        "detail": f"{source.name}: {structure} {metric} is relative but no Total dose was exported; metric not assessed.",
                    })
                continue
            value, unit, conversion = converted
            metrics.append({
                "patient_id": patient_id,
                "plan": plan,
                "course": course,
                "total_dose_gy": total_dose,
                "original_structure": structure,
                "metric": metric,
                "value": value,
                "unit": unit,
                "source_value": raw_value,
                "source_unit": source_unit,
                "conversion": conversion,
                "source_file": source.name,
            })
        if curve_index is None:
            continue
        curve_header = section[curve_index].strip()
        relative_first = bool(re.match(r"^\s*Relative\s+dose", curve_header, re.IGNORECASE))
        unit_matches = re.findall(r"(?i)(Relative\s+dose|(?<!Relative\s)dose)\s*\[([^\]]+)\]", curve_header)
        absolute_unit = next((unit for name, unit in unit_matches if _norm(name) == "DOSE"), "Gy")
        curve_scale = 0.01 if _norm(absolute_unit) == "CGY" else 1.0
        points: list[dict[str, float]] = []
        for line in section[curve_index + 1:]:
            values = re.findall(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[Ee][-+]?\d+)?", line)
            if len(values) not in {2, 3}:
                if points:
                    break
                continue
            parsed_values = [_numeric(value) for value in values]
            if any(value is None for value in parsed_values):
                continue
            numeric_values = [float(value) for value in parsed_values]
            if len(numeric_values) == 3:
                first, second, volume = numeric_values
                relative, dose = (first, second) if relative_first else (second, first)
            else:
                first, volume = numeric_values
                if relative_first:
                    relative = first
                    if total_dose is None:
                        continue
                    dose = relative * total_dose / (100.0 * curve_scale)
                else:
                    dose = first
                    relative = 100.0 * dose * curve_scale / total_dose if total_dose else None
            points.append({"dose_gy": dose * curve_scale, "relative_dose_pct": relative, "volume_pct": volume})
        if points:
            curves.append({
                "patient_id": patient_id,
                "plan": plan,
                "original_structure": structure,
                "display_order": "relative_first" if relative_first else "dose_first",
                "source_file": source.name,
                "points": points,
            })
    return ParsedEclipseFile(source, patient_id, plan, course, total_dose, metrics, curves, issues)


def _role_map(structure_roles: dict[str, str | list[str]]) -> dict[str, str]:
    output: dict[str, str] = {}
    for role, names in structure_roles.items():
        canonical_role = ROLE_CANONICAL.get(role, role)
        values = names if isinstance(names, list) else [names]
        for name in values:
            normalized = _norm(str(name))
            if normalized and normalized in output and output[normalized] != canonical_role:
                raise ValueError(
                    f"ECLIPSE_DVH_MAPPING: structure {name!r} is assigned to both "
                    f"{output[normalized]} and {canonical_role}."
                )
            if normalized:
                output[normalized] = canonical_role
    return output


def normalise_eclipse_dvh_source(
    source: str | Path,
    structure_roles: dict[str, str | list[str]],
    expected_patient_id: str | None = None,
    expected_plan: str | None = None,
) -> dict[str, Any]:
    """Normalize eclipse dvh source without changing scientific meaning."""
    root = Path(source)
    paths = sorted(path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".txt") if root.is_dir() else [root]
    if not paths or any(path.suffix.lower() != ".txt" for path in paths):
        raise ValueError("ECLIPSE_DVH_FORMAT: select an Eclipse DVH .txt file or a directory containing .txt exports.")
    parsed = [parse_eclipse_dvh_file(path) for path in paths]
    patient_ids = sorted({item.patient_id for item in parsed})
    plans = sorted({item.plan for item in parsed})
    courses = sorted({item.course for item in parsed if item.course})
    total_doses = sorted({round(item.total_dose_gy, 9) for item in parsed if item.total_dose_gy is not None})
    if len(patient_ids) != 1:
        raise ValueError(f"ECLIPSE_DVH_AMBIGUOUS: multiple Patient IDs found: {patient_ids}")
    if expected_patient_id and _norm(patient_ids[0]) != _norm(expected_patient_id):
        raise ValueError(
            f"ECLIPSE_DVH_IDENTITY: export Patient ID {patient_ids[0]!r} does not match ASCEND case {expected_patient_id!r}."
        )
    if len(plans) != 1 or len(courses) > 1 or len(total_doses) > 1:
        raise ValueError(
            "ECLIPSE_DVH_AMBIGUOUS: select exactly one course, plan, and normalization dose; "
            f"courses={courses}, plans={plans}, total_dose_gy={total_doses}"
        )
    if expected_plan and _norm(plans[0]) != _norm(expected_plan):
        raise ValueError(
            f"ECLIPSE_DVH_IDENTITY: export Plan {plans[0]!r} does not match selected RTPLAN {expected_plan!r}."
        )

    role_by_name = _role_map(structure_roles)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for report in parsed:
        for metric in report.metrics:
            grouped.setdefault((_norm(metric["original_structure"]), metric["metric"]), []).append(metric)
    normalized_metrics: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = [issue for report in parsed for issue in report.issues]
    structure_variants: dict[str, set[str]] = {}
    for report in parsed:
        for metric in report.metrics:
            structure_variants.setdefault(_norm(metric["original_structure"]), set()).add(metric["original_structure"])
    collisions = [sorted(names) for names in structure_variants.values() if len(names) > 1]
    if collisions:
        raise ValueError(f"ECLIPSE_DVH_AMBIGUOUS: structure names collide after normalization: {collisions}")
    rank = {"direct_gy": 0, "direct_volume": 0, "cgy_to_gy": 1, "relative_percent_of_total_dose": 2}
    for (_structure_key, metric_name), candidates in sorted(grouped.items()):
        best_rank = min(rank.get(item["conversion"], 99) for item in candidates)
        preferred = [item for item in candidates if rank.get(item["conversion"], 99) == best_rank]
        values = [float(item["value"]) for item in preferred]
        tolerance = 0.001 if preferred[0]["unit"] == "Gy" else 0.01
        if max(values) - min(values) > tolerance:
            issues.append({
                "severity": "BLOCK",
                "code": "conflicting_preferred_values",
                "detail": f"{preferred[0]['original_structure']} {metric_name}: preferred exports disagree ({min(values):.6g} to {max(values):.6g} {preferred[0]['unit']}).",
            })
            continue
        selected = sorted(preferred, key=lambda item: item["source_file"])[0]
        all_values = [float(item["value"]) for item in candidates]
        role = role_by_name.get(_norm(selected["original_structure"]))
        normalized_metrics.append({
            "patient_id": selected["patient_id"],
            "plan": selected["plan"],
            "course": selected["course"],
            "total_dose_gy": selected["total_dose_gy"],
            "original_structure": selected["original_structure"],
            "ascend_role": role or "UNMAPPED_SUPPORTING",
            "metric": metric_name,
            "value": statistics.median(values),
            "unit": selected["unit"],
            "preferred_conversion": selected["conversion"],
            "preferred_source_count": len(preferred),
            "all_source_count": len(candidates),
            "corroboration_range": max(all_values) - min(all_values),
            "source_files": sorted({item["source_file"] for item in candidates}),
        })

    curve_groups: dict[str, list[dict[str, Any]]] = {}
    for report in parsed:
        for curve in report.curves:
            curve_groups.setdefault(_norm(curve["original_structure"]), []).append(curve)
    normalized_curves: list[dict[str, Any]] = []
    for curves in curve_groups.values():
        preferred = [curve for curve in curves if curve["display_order"] == "dose_first"] or curves
        chosen = sorted(preferred, key=lambda item: item["source_file"])[0]
        normalized_curves.append({**chosen, "source_files": sorted({item["source_file"] for item in curves})})

    if any(item["severity"] == "BLOCK" for item in issues):
        details = "; ".join(item["detail"] for item in issues if item["severity"] == "BLOCK")
        raise ValueError(f"ECLIPSE_DVH_AMBIGUOUS: {details}")
    return {
        "format": "eclipse_cumulative_dvh_text",
        "patient_id": patient_ids[0],
        "plan": plans[0],
        "courses": courses,
        "total_dose_gy": total_doses[0] if total_doses else None,
        "source_root": str(root),
        "source_files": [
            {"name": item.path.name, "path": str(item.path), "sha256": _sha256(item.path)} for item in parsed
        ],
        "metrics": normalized_metrics,
        "curves": normalized_curves,
        "issues": issues,
        "summary": {
            "files_read": len(parsed),
            "unique_structures": len({_norm(item["original_structure"]) for item in normalized_metrics}),
            "normalized_metrics": len(normalized_metrics),
            "normalized_curves": len(normalized_curves),
            "redundant_metric_observations": sum(item["all_source_count"] for item in normalized_metrics) - len(normalized_metrics),
        },
    }


def write_legacy_gtv_csv(imported: dict[str, Any], path: str | Path) -> Path:
    """Write legacy gtv csv deterministically to disk."""
    target = Path(path)
    rows = [item for item in imported["metrics"] if item["ascend_role"] == "GTV" and item["metric"] in SUPPORTED_METRICS]
    if not rows:
        raise ValueError("ECLIPSE_DVH_MAPPING: no Eclipse structure maps to the configured GTV role.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["structure_name", "metric_name", "value", "unit"])
        writer.writeheader()
        for item in rows:
            writer.writerow({"structure_name": "GTV", "metric_name": item["metric"], "value": f"{item['value']:.12g}", "unit": item["unit"]})
    return target


def compare_eclipse_to_layer1(imported: dict[str, Any], result: Any, geometry: dict[str, Any], validated: Any) -> list[dict[str, Any]]:
    """Compare eclipse to layer1 and retain auditable evidence."""
    original_to_standard = {_norm(item["original_name"]): item["standard_name"] for item in result.mappings}
    standards_to_originals: dict[str, list[str]] = {}
    for original, standard in original_to_standard.items():
        standards_to_originals.setdefault(standard, []).append(original)
    summaries = {item["Structure"]: item for item in result.dvh_summary}
    voxel_cc = validated.voxel_volume_cc(geometry)
    calculated: dict[str, dict[str, float]] = {}
    for standard, mask in result.mask_arrays.items():
        if mask.any() and mask.shape == result.dose_array_gy.shape:
            values = validated.independent_metrics(result.dose_array_gy, mask, voxel_cc)
            summary = summaries.get(standard, {})
            calculated[standard] = {
                "Volume": float(summary.get("Volume_cc", values["Volume_cc"])),
                "D95": float(summary.get("DoseCover_D95_Gy", values["D95_Gy"])),
                "D2": float(values["D2_Gy"]),
                "Dmin": float(summary.get("MinDose_Gy", values["Dmin_Gy"])),
                "Dmax": float(summary.get("MaxDose_Gy", values["Dmax_Gy"])),
                "Dmean": float(summary.get("MeanDose_Gy", values["Dmean_Gy"])),
            }
    audit: list[dict[str, Any]] = []
    for reference in imported["metrics"]:
        original = reference["original_structure"]
        standard = original_to_standard.get(_norm(original))
        metric = reference["metric"]
        row = {
            "original_structure": original,
            "ascend_role": reference["ascend_role"],
            "validated_structure": standard,
            "metric": metric,
            "eclipse_value": reference["value"],
            "ascend_value": None,
            "difference": None,
            "tolerance": None,
            "unit": reference["unit"],
            "status": "NOT_ASSESSED",
            "reason": None,
        }
        if not standard or standard not in calculated:
            row["reason"] = "No validated Layer 1 structure mapping or sampled mask."
        elif len(standards_to_originals.get(standard, [])) > 1:
            row["reason"] = "Multiple RTSTRUCT ROIs were combined into this validated mask; one-to-one TPS comparison is unsafe."
        elif metric not in calculated[standard]:
            row["reason"] = "Metric is not supported by the Layer 1 external audit."
        else:
            reference_value = float(reference["value"])
            ascend_value = calculated[standard][metric]
            tolerance = max(0.2, 0.02 * abs(reference_value)) if reference["unit"] == "Gy" else 0.03 * abs(reference_value)
            difference = ascend_value - reference_value
            row.update({
                "ascend_value": ascend_value,
                "difference": difference,
                "tolerance": tolerance,
                "status": "PASS" if abs(difference) <= tolerance else "WARN",
            })
        audit.append(row)
    return audit


def write_import_artifacts(imported: dict[str, Any], audit: list[dict[str, Any]], folder: str | Path) -> dict[str, dict[str, Any]]:
    """Write import artifacts deterministically to disk."""
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    metrics_path = target / "eclipse_dvh_normalized.csv"
    metric_fields = [
        "patient_id", "plan", "course", "total_dose_gy", "original_structure", "ascend_role", "metric", "value", "unit",
        "preferred_conversion", "preferred_source_count", "all_source_count", "corroboration_range", "source_files",
    ]
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        for item in imported["metrics"]:
            writer.writerow({**item, "source_files": "|".join(item["source_files"])})
    curves_path = target / "eclipse_dvh_curves.csv"
    with curves_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["patient_id", "plan", "original_structure", "dose_gy", "relative_dose_pct", "volume_pct", "source_files"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for curve in imported["curves"]:
            for point in curve["points"]:
                writer.writerow({
                    "patient_id": curve["patient_id"], "plan": curve["plan"], "original_structure": curve["original_structure"],
                    **point, "source_files": "|".join(curve["source_files"]),
                })
    audit_path = target / "eclipse_dvh_audit.csv"
    with audit_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["original_structure", "ascend_role", "validated_structure", "metric", "eclipse_value", "ascend_value", "difference", "tolerance", "unit", "status", "reason"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(audit)
    manifest_path = target / "eclipse_dvh_import.json"
    manifest = {key: value for key, value in imported.items() if key not in {"metrics", "curves"}}
    manifest["normalized_metric_records"] = len(imported["metrics"])
    manifest["normalized_curve_records"] = sum(len(item["points"]) for item in imported["curves"])
    manifest["audit_summary"] = {
        status: sum(item["status"] == status for item in audit) for status in ("PASS", "WARN", "NOT_ASSESSED")
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        name: {"path": str(path), "sha256": _sha256(path)}
        for name, path in {
            "normalized_metrics": metrics_path,
            "normalized_curves": curves_path,
            "comparison_audit": audit_path,
            "import_manifest": manifest_path,
        }.items()
    }
