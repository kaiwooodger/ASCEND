# ASCEND 1.6.8

Release identifier: `ASCEND-1.6.8-SELECTABLE-COHERENT-PDF-REPORT-20260909`

ASCEND 1.6.8 adds a selectable, human-readable PDF report to the workstation Export screen.

## Export workflow

The Export screen groups report content into overview, Layer 2.1 primary metrics, supporting physical evidence, biological and modelled results, and audit trail sections. Each section has an independent checkbox. The six locked Layer 2.1 primary metrics can be selected individually. Select-all and clear-all controls support full and minimal reports.

The selection is stored in `pdf_report_options` in the case configuration. Export creates one A4 PDF at the user-selected destination.

## Report structure

The report contains a title page, case and workflow context, numbered selected sections, repeated table headers, page numbers, warnings, limitations, and provenance. Missing or unrun results are identified explicitly instead of being omitted silently.

## Data integrity

PDF export reads current stored result objects and does not recalculate scientific metrics. Export refuses an empty selection, unknown option identifiers, and stale stored results. JSON remains the authoritative machine-readable export.

Layer 1 uses compact same-filesystem staging names for archive generation, formal publication, cache publication, and cache materialisation. This prevents ASCEND's temporary naming scheme from exceeding the legacy Windows path limit while preserving atomic publication and cleanup semantics.

## Scientific continuity

The 1.6.6 interval-by-interval RTPLAN control-point beam-on calculation remains unchanged. Physical dose, Layer 2, Layer 3, identity-bound anatomical-mask rules, and locked validation definitions are unchanged.
