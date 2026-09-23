"""Human-readable PDF reporting over current stored ASCEND results."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import math
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle,
)

from ascend import __validation_scope__, __version__
from ascend.models.case import ASCENDCase
from ascend.reporting.eligibility import require_current_result
from ascend.report_options import PDF_REPORT_OPTIONS


_DASH_TRANSLATION = str.maketrans({character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"})
_MAX_CELL_TEXT = 1_600
_MAX_COLLECTION_ITEMS = 12
_MAX_VALUE_DEPTH = 3


def _safe_text(value: Any) -> str:
    return str(value).translate(_DASH_TRANSLATION)


def _bounded_text(value: Any) -> str:
    text = _safe_text(value).replace("_", " ")
    if len(text) <= _MAX_CELL_TEXT:
        return text
    omitted = len(text) - _MAX_CELL_TEXT
    return f"{text[:_MAX_CELL_TEXT].rstrip()} ... [{omitted} characters omitted from PDF; retained in stored results]"


def _value(value: Any, depth: int = 0) -> str:
    if value is None or value == "":
        return "Not available"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "Not finite"
        return f"{value:.6g}"
    if depth >= _MAX_VALUE_DEPTH and isinstance(value, (list, tuple, set, dict)):
        return f"{len(value)} stored item(s); full values retained in structured export"
    if isinstance(value, (list, tuple, set)):
        if not value:
            return "None"
        items = list(value)
        preview = ", ".join(_value(item, depth + 1) for item in items[:_MAX_COLLECTION_ITEMS])
        if len(items) > _MAX_COLLECTION_ITEMS:
            preview += f", ... [{len(items) - _MAX_COLLECTION_ITEMS} more item(s); retained in stored results]"
        return _bounded_text(preview)
    if isinstance(value, dict):
        if not value:
            return "None"
        items = list(value.items())
        preview = "; ".join(
            f"{_label(key)}: {_value(item, depth + 1)}" for key, item in items[:_MAX_COLLECTION_ITEMS]
        )
        if len(items) > _MAX_COLLECTION_ITEMS:
            preview += f"; ... [{len(items) - _MAX_COLLECTION_ITEMS} more field(s); retained in stored results]"
        return _bounded_text(preview)
    return _bounded_text(value)


def _label(value: Any) -> str:
    return _safe_text(value).replace("_", " ").strip().title()


def _p(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_value(value)), style)


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    if isinstance(value, dict) and depth < 2:
        rows: list[tuple[str, Any]] = []
        for key, item in value.items():
            name = f"{prefix} - {_label(key)}" if prefix else _label(key)
            if isinstance(item, dict):
                rows.extend(_flatten(item, name, depth + 1))
            elif isinstance(item, list) and item and all(isinstance(entry, dict) for entry in item):
                rows.append((name, f"{len(item)} record(s)"))
            else:
                rows.append((name, item))
        return rows
    return [(prefix or "Value", value)]


class _Report:
    def __init__(self, case: ASCENDCase, selected: set[str]) -> None:
        self.case = case
        self.selected = selected
        base = getSampleStyleSheet()
        navy = colors.HexColor("#16364A")
        teal = colors.HexColor("#177A82")
        self.styles = {
            "title": ParagraphStyle("Title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24,
                                    leading=29, textColor=navy, alignment=TA_LEFT, spaceAfter=8),
            "subtitle": ParagraphStyle("Subtitle", parent=base["Normal"], fontSize=11, leading=16,
                                       textColor=colors.HexColor("#45616F"), spaceAfter=10),
            "h1": ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16,
                                 leading=20, textColor=navy, spaceBefore=8, spaceAfter=8, keepWithNext=True),
            "h2": ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11,
                                 leading=14, textColor=teal, spaceBefore=7, spaceAfter=5, keepWithNext=True),
            "body": ParagraphStyle("Body", parent=base["BodyText"], fontSize=8.5, leading=12,
                                   textColor=colors.HexColor("#24343D"), spaceAfter=5),
            "small": ParagraphStyle("Small", parent=base["BodyText"], fontSize=7.2, leading=9.2,
                                    textColor=colors.HexColor("#3E505A")),
            "cell": ParagraphStyle("Cell", parent=base["BodyText"], fontSize=7.1, leading=8.8,
                                   textColor=colors.HexColor("#24343D")),
            "cell_head": ParagraphStyle("CellHead", parent=base["BodyText"], fontName="Helvetica-Bold",
                                        fontSize=7.2, leading=9, textColor=colors.white, alignment=TA_LEFT),
            "notice": ParagraphStyle("Notice", parent=base["BodyText"], fontSize=8, leading=11,
                                     textColor=colors.HexColor("#743C00"), backColor=colors.HexColor("#FFF2D9"),
                                     borderPadding=7, spaceBefore=5, spaceAfter=8),
            "cover": ParagraphStyle("Cover", parent=base["BodyText"], fontSize=10, leading=15,
                                    textColor=colors.HexColor("#24343D"), alignment=TA_CENTER),
        }
        self.story: list[Any] = []
        self.section_number = 0

    def heading(self, title: str, level: int = 1) -> None:
        if level == 1:
            self.section_number += 1
            title = f"{self.section_number}. {title}"
        self.story.append(Paragraph(escape(_safe_text(title)), self.styles["h1" if level == 1 else "h2"]))

    def note(self, text: str, *, warning: bool = False) -> None:
        self.story.append(Paragraph(escape(_safe_text(text)), self.styles["notice" if warning else "body"]))

    def table(self, headers: Iterable[Any], rows: Iterable[Iterable[Any]], widths: list[float] | None = None) -> None:
        material = [list(row) for row in rows]
        if not material:
            self.note("No stored records are available for this selected section.")
            return
        data = [[_p(item, self.styles["cell_head"]) for item in headers]]
        data.extend([[_p(item, self.styles["cell"]) for item in row] for row in material])
        table = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#177A82")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B7C8D0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F7F8")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        self.story.extend([table, Spacer(1, 5 * mm)])

    def pairs(self, values: Iterable[tuple[Any, Any]]) -> None:
        self.table(("Item", "Stored value"), values, [58 * mm, 119 * mm])

    def histogram(self, title: str, histogram: Any) -> None:
        if not isinstance(histogram, dict):
            return
        bins = histogram.get("bin_values")
        volumes = histogram.get("volume_pct")
        if not isinstance(bins, list) or not isinstance(volumes, list) or not bins or not volumes:
            return
        self.heading(title, 2)
        units = histogram.get("units") or "Dose"
        self.note(
            f"{_value(histogram.get('histogram_type') or 'Cumulative volume histogram')}; "
            f"{len(bins)} stored bins. Full entries follow and may continue across pages."
        )
        rows = [
            (index, bins[index], volumes[index] if index < len(volumes) else "Not available")
            for index in range(len(bins))
        ]
        self.table(("Bin", f"Threshold ({units})", "Volume (%)"), rows, [24 * mm, 76 * mm, 77 * mm])

    def section_overview(self) -> None:
        self.heading("Case and workflow overview")
        config = self.case.configuration
        self.pairs([
            ("Case identifier", self.case.case_id),
            ("Patient identifier", self.case.patient_metadata.get("patient_id")),
            ("Treatment delivery mode", config.treatment_delivery_mode),
            ("Dose context", config.dose_context),
            ("Prescription context", config.prescription_context),
            ("Rx_L", f"{_value(config.prescriptions['Rx_L'].gy)} Gy in {_value(config.prescriptions['Rx_L'].fractions)} fraction(s)"),
            ("Rx_H", f"{_value(config.prescriptions['Rx_H'].gy)} Gy in {_value(config.prescriptions['Rx_H'].fractions)} fraction(s)"),
            ("Configuration hash", self.case.configuration_hash),
        ])
        self.heading("Workflow status", 2)
        self.table(("Layer", "Calculation status", "Interpretation status", "Error / stale reason"), [
            (name.replace("_", "."), record.calculation_status, record.interpretation_status,
             record.error or record.stale_reason or "None")
            for name, record in (("Layer 1", self.case.layer1), ("Layer 2.1", self.case.layer2_1),
                                 ("Layer 2.2", self.case.layer2_2), ("Layer 3.1", self.case.layer3_1),
                                 ("Layer 3.2", self.case.layer3_2))
        ], [24 * mm, 40 * mm, 43 * mm, 70 * mm])

    def section_rtplan(self) -> None:
        self.heading("RTPLAN delivery and beam-on time")
        layer1 = self.case.layer1.result or {}
        delivery = (layer1.get("manifest") or {}).get("rtplan_delivery") or (
            self.case.provenance.get("dicom_configuration_prefill", {}).get("delivery_metadata", {})
        )
        if delivery.get("status") != "available":
            self.note("No RTPLAN delivery metadata is available.")
            return
        self.pairs([
            ("Plan label", delivery.get("plan_label")), ("Plan UID", delivery.get("plan_uid")),
            ("Treatment beams", delivery.get("treatment_beam_count")), ("VMAT arcs", delivery.get("vmat_arc_count")),
            ("MU per fraction", delivery.get("total_mu_per_fraction")), ("Total planned MU", delivery.get("total_planned_mu")),
            ("Control-point beam-on per fraction", f"{_value(delivery.get('beam_on_time_seconds_per_fraction'))} s"),
            ("Total planned beam-on", f"{_value(delivery.get('total_beam_on_time_seconds'))} s"),
        ])
        self.note("Planned control-point beam-on time excludes imaging, setup, inter-beam delays, and mechanical-transition overhead.", warning=True)
        self.heading("Beam summary", 2)
        self.table(("Beam", "Technique", "Group", "MU", "Energy MV", "Dose rate MU/min", "Beam-on s"), [
            (f"{beam.get('beam_number')}: {beam.get('beam_name')}", beam.get("delivery_technique"),
             beam.get("fraction_group_numbers"), beam.get("meterset_mu"), beam.get("nominal_energy_mv"),
             beam.get("dose_rate_mu_per_min"), beam.get("beam_on_time_seconds"))
            for beam in delivery.get("beams", [])
        ], [31 * mm, 24 * mm, 18 * mm, 19 * mm, 21 * mm, 34 * mm, 25 * mm])
        for beam in delivery.get("beams", []):
            calculation = beam.get("beam_on_time_calculation") or {}
            self.heading(f"Beam {beam.get('beam_number')} control-point intervals", 2)
            if calculation.get("status") != "calculated":
                self.note(f"Beam-on time not calculated: {_value(calculation.get('reason'))}", warning=True)
            self.table(("CP start", "CP end", "Delta weight", "Delta MU", "Rate MU/min", "Rate source CP", "Seconds"), [
                (segment.get("start_control_point_index"), segment.get("end_control_point_index"),
                 segment.get("delta_meterset_weight"), segment.get("delta_meterset_mu"),
                 segment.get("dose_rate_mu_per_min"), segment.get("dose_rate_source_control_point_index"),
                 segment.get("beam_on_time_seconds"))
                for segment in calculation.get("segments", [])
            ], [20 * mm, 20 * mm, 28 * mm, 25 * mm, 30 * mm, 29 * mm, 25 * mm])

    def section_layer1(self) -> None:
        self.heading("Layer 1 validation")
        result = self.case.layer1.result or {}
        self.table(("Level", "Check", "Finding", "Blocks"), [
            (item.get("level"), item.get("check"), item.get("detail"), item.get("blocks"))
            for item in result.get("findings", [])
        ], [19 * mm, 42 * mm, 96 * mm, 20 * mm])
        self.heading("Structure mapping", 2)
        self.table(("Original structure", "ASCEND structure", "ROI number", "Status"), [
            (item.get("original_name"), item.get("standard_name") or item.get("canonical_mapping"),
             item.get("roi_number") or (item.get("roi_identity") or {}).get("roi_number"), item.get("mapping_status"))
            for item in result.get("structure_mapping", [])
        ], [53 * mm, 55 * mm, 27 * mm, 42 * mm])

    def section_eclipse(self) -> None:
        self.heading("Eclipse reference comparison")
        audit = (self.case.layer1.result or {}).get("eclipse_dvh_audit", [])
        self.table(("Structure", "Role", "Metric", "Eclipse", "ASCEND", "Difference", "Unit", "Status"), [
            (item.get("original_structure"), item.get("ascend_role"), item.get("metric"), item.get("eclipse_value"),
             item.get("ascend_value"), item.get("difference"), item.get("unit"), item.get("status"))
            for item in audit
        ], [28 * mm, 20 * mm, 20 * mm, 23 * mm, 23 * mm, 24 * mm, 14 * mm, 25 * mm])

    def section_layer21_metrics(self) -> None:
        selected_ids = {item.split(":", 1)[1] for item in self.selected if item.startswith("metric:")}
        if not selected_ids:
            return
        self.heading("Selected Layer 2.1 primary metrics")
        metrics = [item for item in (self.case.layer2_1.result or {}).get("harmonised_metrics", []) if item.get("metric_id") in selected_ids]
        self.table(("Metric", "Value", "Units", "Applicability", "Definition", "Warnings"), [
            (_label(item.get("metric_id")), item.get("value"), item.get("units"), item.get("applicability"),
             item.get("definition") or item.get("formula"), item.get("warnings")) for item in metrics
        ], [36 * mm, 22 * mm, 18 * mm, 28 * mm, 50 * mm, 23 * mm])

    def section_layer21_support(self) -> None:
        result = self.case.layer2_1.result or {}
        supporting = result.get("supporting_outputs", {})
        mapping = {
            "layer21_coverage": ("Coverage and volume context", ("high_dose_coverage_context", "high_dose_volume_fraction_context")),
            "layer21_peak_valley": ("Peak, valley, and ratio context", ("peak_valley_dose_context", "ratio_context")),
            "layer21_protocol": ("Protocol-native endpoints", ("protocol_native_endpoint_status", "protocol_native_metrics")),
            "layer21_oar": ("OAR and target geometry", ("oar_vertex_geometry",)),
        }
        for option, (title, keys) in mapping.items():
            if option not in self.selected:
                continue
            self.heading(title)
            for key in keys:
                value = supporting.get(key)
                if isinstance(value, list):
                    self.table(("Record", "Stored values"), [(index, item) for index, item in enumerate(value, 1)], [22 * mm, 155 * mm])
                elif value:
                    self.pairs(_flatten(value))
        if "layer21_per_vertex" in self.selected:
            self.heading("Per-vertex QA")
            records = supporting.get("per_vertex_qa", [])
            self.table(("Vertex", "Volume cc", "Dmean Gy", "D95 Gy", "Dmax Gy", "V95 Rx_H %", "FWHM mm", "Nearest mm"), [
                (item.get("vertex_id") or item.get("id"), item.get("volume_cc") or item.get("vertex_volume_cc"),
                 item.get("mean_dose_gy"), item.get("d95_gy"), item.get("maximum_dose_gy") or item.get("max_dose_gy"),
                 item.get("v95_rxh_percent"), item.get("local_fwhm_mm"), item.get("nearest_vertex_distance_mm"))
                for item in records
            ], [28 * mm, 20 * mm, 22 * mm, 20 * mm, 20 * mm, 25 * mm, 22 * mm, 20 * mm])

    def section_layer22(self) -> None:
        result = self.case.layer2_2.result or {}
        if "layer22_graph" in self.selected:
            self.heading("Layer 2.2 spatial graph")
            self.pairs(_flatten(result.get("plan_ipvdr") or {}))
            self.table(("Edge", "Start", "End", "Distance mm", "iPVDR"), [
                (item.get("edge_id"), item.get("source") or item.get("start_vertex_id"),
                 item.get("target") or item.get("end_vertex_id"), item.get("distance_mm"),
                 item.get("ipvdr") or item.get("primary_ipvdr")) for item in result.get("edges", [])
            ], [32 * mm, 30 * mm, 30 * mm, 42 * mm, 43 * mm])
        extensions = result.get("layer2_2_extensions", {})
        if "layer22_gradient" in self.selected:
            self.heading("ICRU 91 dose-gradient evidence")
            self.pairs(_flatten(extensions.get("dose_gradient") or {}))
        if "layer22_saddle" in self.selected:
            self.heading("Saddle-path evidence")
            saddle = extensions.get("saddle_graph") or {}
            self.table(("Edge", "Saddle dose Gy", "Saddle PVDR", "Status", "Warnings"), [
                (item.get("edge_id"), item.get("saddle_dose_gy"), item.get("saddle_pvdr"), item.get("status"), item.get("warnings"))
                for item in saddle.get("edges", [])
            ], [33 * mm, 34 * mm, 34 * mm, 32 * mm, 44 * mm])

    def section_layer31(self) -> None:
        result = self.case.layer3_1.result or {}
        if "layer31_roi" in self.selected:
            self.heading("Layer 3.1 ROI BED and EQD2 metrics")
            roi_results = result.get("roi_results", [])
            self.table(("ROI", "ROI number", "Alpha/beta Gy", "Stored biological metrics"), [
                ((item.get("assignment") or {}).get("roi_name"), ((item.get("assignment") or {}).get("roi_identity") or {}).get("roi_number"),
                 (item.get("assignment") or {}).get("alpha_beta_gy"), item.get("metrics")) for item in roi_results
            ], [39 * mm, 26 * mm, 29 * mm, 83 * mm])
            for item in roi_results:
                assignment = item.get("assignment") or {}
                roi_name = assignment.get("roi_name") or "Unnamed ROI"
                self.histogram(f"{roi_name} BED-volume histogram", item.get("bed_volume_histogram"))
                self.histogram(f"{roi_name} EQD2-volume histogram", item.get("eqd2_volume_histogram"))
        branches = (
            ("layer31_tumour", "Layer 3.1B tumour response", "layer3_1b_high_dose_sfrt_response"),
            ("layer31_oar", "Layer 3.1C therapeutic ratio and OAR EUD / SF", "layer3_1c_modelled_therapeutic_ratio"),
            ("layer31_tcp", "Layer 3.1D tumour control probability", "layer3_1d_tumour_control_probability"),
        )
        for option, title, key in branches:
            if option in self.selected:
                self.heading(title)
                self.pairs(_flatten(result.get(key) or {}))
                if option == "layer31_tumour":
                    regional = ((result.get(key) or {}).get("regional_survival") or {})
                    self.heading("Regional tumour survival decomposition", 2)
                    self.note(
                        "Mean surviving fraction (SF) is dimensionless within each region. "
                        "Survivor contribution is the region's share of modelled surviving tumour cells; "
                        "the three contributions should sum to 100%."
                    )
                    region_names = {"H": "Vertex (H)", "V": "Valley (V)", "O": "Remaining tumour (O)"}
                    self.table(
                        ("Region", "Voxels", "Tumour volume (%)", "Mean SF", "Survivor contribution (%)"),
                        [
                            (
                                region_names.get(item.get("region_id"), item.get("region_id")),
                                item.get("voxel_count"),
                                100 * item["tumour_volume_fraction"] if item.get("tumour_volume_fraction") is not None else None,
                                item.get("mean_surviving_fraction"),
                                100 * item["survivor_contribution_fraction"] if item.get("survivor_contribution_fraction") is not None else None,
                            )
                            for item in regional.get("records", [])
                        ],
                        [39 * mm, 20 * mm, 35 * mm, 30 * mm, 53 * mm],
                    )
                    if regional.get("records"):
                        self.note(
                            f"Contribution sum: {_value(100 * regional['contribution_sum'] if regional.get('contribution_sum') is not None else None)}%; "
                            f"sum residual: {_value(100 * regional['sum_residual'] if regional.get('sum_residual') is not None else None)} percentage points."
                        )
                    sensitivity = result.get("layer3_1b_tumour_alpha_beta_sensitivity") or {}
                    if sensitivity.get("enabled"):
                        self.heading("Tumour-site alpha/beta sensitivity", 2)
                        self.note(
                            f"Exploratory range for {_value(sensitivity.get('tumour_site'))}. "
                            f"Scaling rule: {_value(sensitivity.get('parameter_scaling'))}. "
                            "Dose, fraction history, delivery time, and all other MLQ parameters remain fixed."
                        )
                        self.table(
                            ("Alpha/beta (Gy)", "Alpha (Gy-1)", "Beta (Gy-2)", "SF2", "Mean tumour SF", "Tumour EUD (Gy)"),
                            [
                                (
                                    item.get("alpha_beta_gy"), item.get("alpha_per_gy"), item.get("beta_per_gy2"),
                                    item.get("sf2"), item.get("mean_tumour_survival_fraction"), item.get("tumour_eud_gy"),
                                )
                                for item in sensitivity.get("records", [])
                            ],
                            [31 * mm, 28 * mm, 28 * mm, 27 * mm, 33 * mm, 30 * mm],
                        )
                if option == "layer31_oar":
                    summary = (result.get(key) or {}).get("oar_eud_summary") or {}
                    self.heading("OAR EUD and surviving fraction (SF)", 2)
                    self.note("Stored normal-tissue MLQ analysis. EUD is in Gy; mean SF is dimensionless. These values are not NTCP or toxicity predictions.")
                    self.table(("OAR", "Classification", "Volume (cm3)", "Mean OAR SF", "OAR EUD (Gy)", "State"), [
                        (item.get("oar_name"), item.get("classification"), item.get("dose_sampled_volume_cc"),
                         item.get("mean_normal_tissue_survival_fraction"), item.get("normal_tissue_eud_gy"),
                         (item.get("solver") or {}).get("solver_status") or summary.get("applicability_status"))
                        for item in summary.get("records", [])
                    ], [34 * mm, 30 * mm, 24 * mm, 28 * mm, 28 * mm, 33 * mm])
                    if summary.get("reason"):
                        self.note(str(summary["reason"]))
        if "layer31_six_metrics" in self.selected:
            self.heading("Biological six-metric comparison")
            records = (result.get("biological_six_metrics") or {}).get("records", [])
            self.table(("Metric", "Physical", "Geometry", "BED", "EQD2", "Applicability"), [
                (item.get("metric_id"), item.get("physical_metric_reference"), item.get("geometry"),
                 item.get("bed"), item.get("eqd2"), item.get("applicability")) for item in records
            ], [32 * mm, 29 * mm, 29 * mm, 29 * mm, 29 * mm, 29 * mm])

    def section_layer32(self) -> None:
        if "layer32_nonlocal" not in self.selected:
            return
        self.heading("Layer 3.2 non-local model results")
        result = self.case.layer3_2.result or {}
        if not self.case.configuration.layer32_enabled:
            self.note("Layer 3.2 is disabled and is not assessed.")
            return
        self.pairs(_flatten({key: value for key, value in result.items() if key not in {"edge_metrics", "peri_gtv_spill_shells", "oar_biological_spill", "artifacts"}}))
        for title, key in (("Graph edge metrics", "edge_metrics"), ("Peri-GTV spill shells", "peri_gtv_spill_shells"),
                           ("OAR biological spill", "oar_biological_spill")):
            self.heading(title, 2)
            self.table(("Record", "Stored values"), [(index, item) for index, item in enumerate(result.get(key, []), 1)], [22 * mm, 155 * mm])

    def section_audit(self) -> None:
        self.heading("Warnings, limitations, and provenance")
        warning_rows = []
        for name, record in (("Layer 1", self.case.layer1), ("Layer 2.1", self.case.layer2_1),
                             ("Layer 2.2", self.case.layer2_2), ("Layer 3.1", self.case.layer3_1),
                             ("Layer 3.2", self.case.layer3_2)):
            warning_rows.extend((name, warning) for warning in record.warnings)
            if record.error:
                warning_rows.append((name, f"ERROR: {record.error}"))
        warning_rows.extend(("Case", warning) for warning in self.case.warnings)
        self.table(("Source", "Warning or limitation"), warning_rows, [30 * mm, 147 * mm])
        self.heading("Provenance", 2)
        self.pairs([
            ("ASCEND version", __version__), ("Validation scope", __validation_scope__),
            ("Configuration hash", self.case.configuration_hash),
            ("Layer 1 run ID", self.case.layer1.run_id), ("Layer 2.1 run ID", self.case.layer2_1.run_id),
            ("Layer 2.2 run ID", self.case.layer2_2.run_id), ("Layer 3.1 run ID", self.case.layer3_1.run_id),
            ("Layer 3.2 run ID", self.case.layer3_2.run_id),
            *[(f"Case provenance - {_label(key)}", value) for key, value in self.case.provenance.items()],
        ])

    def build(self) -> list[Any]:
        self.story.extend([
            Spacer(1, 24 * mm), Paragraph("ASCEND Analysis Report", self.styles["title"]),
            Paragraph(f"Case {_value(self.case.case_id)}", self.styles["subtitle"]), Spacer(1, 10 * mm),
            Paragraph(
                escape(f"Generated by ASCEND {__version__} on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"),
                self.styles["cover"],
            ), Spacer(1, 8 * mm),
            Paragraph(escape(_safe_text(__validation_scope__)), self.styles["cover"]), Spacer(1, 10 * mm),
            Paragraph(
                "This report presents selected stored ASCEND results. It does not recalculate scientific metrics. "
                "Statuses, warnings, applicability, and provenance remain part of the interpretation.",
                self.styles["notice"],
            ),
            Paragraph("Selected report contents", self.styles["h2"]),
            Paragraph("<br/>".join(escape(PDF_REPORT_OPTIONS[item]) for item in PDF_REPORT_OPTIONS if item in self.selected), self.styles["body"]),
            PageBreak(),
        ])
        if "overview" in self.selected: self.section_overview()
        if "rtplan_delivery" in self.selected: self.section_rtplan()
        if "layer1_validation" in self.selected: self.section_layer1()
        if "eclipse_audit" in self.selected: self.section_eclipse()
        self.section_layer21_metrics()
        self.section_layer21_support()
        self.section_layer22()
        self.section_layer31()
        self.section_layer32()
        if "warnings_provenance" in self.selected: self.section_audit()
        return self.story


def export_pdf_report(case: ASCENDCase, destination: str | Path, options: Iterable[str]) -> Path:
    """Write one selected, human-readable report without recalculating metrics."""
    selected = set(options)
    unknown = sorted(selected - set(PDF_REPORT_OPTIONS))
    if unknown:
        raise ValueError(f"Unsupported PDF report selections: {', '.join(unknown)}")
    if not selected:
        raise ValueError("Select at least one PDF report item.")
    for name in ("layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"):
        record = getattr(case, name)
        if name == "layer3_2" and not case.configuration.layer32_enabled:
            continue
        if record.result:
            require_current_result(case, record)
    target = Path(destination).expanduser()
    if target.suffix.lower() != ".pdf":
        target = target.with_suffix(".pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def header_footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#B7C8D0")); canvas.setLineWidth(0.4)
        canvas.line(18 * mm, 16 * mm, A4[0] - 18 * mm, 16 * mm)
        canvas.setFont("Helvetica", 7); canvas.setFillColor(colors.HexColor("#526874"))
        canvas.drawString(18 * mm, 10 * mm, _safe_text(f"ASCEND {__version__} | Case {_value(case.case_id)} | {generated}"))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(target), pagesize=A4, rightMargin=17 * mm, leftMargin=17 * mm,
        topMargin=18 * mm, bottomMargin=22 * mm, title=f"ASCEND report - {case.case_id}",
        author="ASCEND", subject="Selected ASCEND analysis results",
    )
    document.build(_Report(case, selected).build(), onFirstPage=header_footer, onLaterPages=header_footer)
    return target
