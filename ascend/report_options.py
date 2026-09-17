"""Stable selectable sections for the coherent ASCEND PDF report."""

PDF_REPORT_OPTION_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Report overview", (("overview", "Case summary and workflow status"), ("rtplan_delivery", "RTPLAN delivery and control-point beam-on time"), ("layer1_validation", "Layer 1 validation findings and structures"), ("eclipse_audit", "Eclipse reference comparison"))),
    ("Layer 2.1 primary metrics", (("metric:peripheral_coverage_v95_rxl", "Peripheral coverage"), ("metric:high_dose_coverage_v95_rxh", "High-dose coverage"), ("metric:high_dose_volume_fraction", "High-dose volume fraction"), ("metric:mean_peak_dose", "Mean peak dose"), ("metric:mean_valley_dose", "Mean valley dose"), ("metric:structure_based_dose_ratio", "Peak-to-valley ratio"))),
    ("Supporting physical evidence", (("layer21_coverage", "Coverage and volume context"), ("layer21_peak_valley", "Peak, valley, and ratio context"), ("layer21_per_vertex", "Per-vertex QA"), ("layer21_protocol", "Protocol-native endpoints"), ("layer21_oar", "OAR and target geometry"), ("layer22_graph", "Layer 2.2 spatial graph"), ("layer22_gradient", "ICRU 91 dose-gradient evidence"), ("layer22_saddle", "Saddle-path evidence"))),
    ("Biological and modelled results", (("layer31_roi", "Layer 3.1 ROI BED and EQD2 metrics"), ("layer31_tumour", "Layer 3.1B tumour response"), ("layer31_oar", "Layer 3.1C therapeutic ratio and OAR EUD / SF"), ("layer31_tcp", "Layer 3.1D TCP"), ("layer31_six_metrics", "Biological six-metric comparison"), ("layer32_nonlocal", "Layer 3.2 non-local model results"))),
    ("Audit trail", (("warnings_provenance", "Warnings, limitations, and provenance"),)),
)

PDF_REPORT_OPTIONS = {option: label for _group, options in PDF_REPORT_OPTION_GROUPS for option, label in options}
DEFAULT_PDF_REPORT_OPTIONS = tuple(PDF_REPORT_OPTIONS)
