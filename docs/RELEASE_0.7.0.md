# ASCEND 0.7.0

Structured Eclipse workflow and selectable outputs baseline. Validated through Layer 3.1.

## Workflow changes

- Protocol endpoints are configured with role, endpoint type, and value controls. No JSON entry is required.
- Supported D-at-volume, V-at-prescription, and V-at-absolute-dose endpoints supplied by Eclipse are auto-filled without replacing explicit user selections.
- Every supplied Eclipse reference remains preserved for audit and export, including unsupported endpoints.
- Layer 2.1 supporting-output categories can be enabled, disabled, and selected for presentation and export.
- Layer 3.1 sensitivity sweeps are optional and support standard, 1 Gy step, 2 Gy step, and custom value modes.
- Historical per-vertex volume aliases are normalised in presentation/export. Current derived and explicit vertex records are regression-tested for positive `volume_cc`.
- Layer 3.1 provides biological counterparts and contextual mappings of the six Layer 2.1 metrics: biological coverage analogues, voxelwise biological transformations, derived peak–valley contrasts, and geometry carried forward.
- Biological coverage uses `0.95 × BED_Rx` and `0.95 × EQD2_Rx` after full component-specific prescription P/Q accumulation. It does not calculate `BED(0.95 × Rx)`.
- High-dose volume fraction is labelled geometry-only and produces no BED/EQD2 value. Same-alpha/beta BED and EQD2 contrast redundancy is explicit.
- Whole-GTV BED/EQD2 mean and D95 are retained as additional LRT-versus-LRT+cERT context.
- Analytic LRT-only, LRT+cERT, same-dose/different-fractionation, and incomplete-composite-prescription tests provide computational verification, not clinical validation.

## Scientific scope

Locked Layer 1, Layer 2.1, and Layer 2.2 scientific source files and formulas are unchanged. Supporting-output filtering, Eclipse endpoint prefill, and sensitivity sweep selection are workflow and presentation operations.
