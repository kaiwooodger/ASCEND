# ASCEND 1.3.1

Consequence-first non-local biological reinterpretation workstation.

Validation scope: Layers 1, 2.1, 2.2, and 3.1 retain their existing validation status. Layer 3.2 remains provisional, hypothesis-generating, not clinically calibrated, and not a toxicity prediction.

## Layer 3.2 presentation and evidence model

- Displays cumulative non-local mediator exposure, `H`, instead of presenting “hazard” as the primary clinical-interface term.
- Uses additional modelled survival reduction relative to LQ, `100[1-exp(-sH)]%`, as the default displayed consequence.
- Separates physical, baseline, mechanism, consequence, and advanced stored fields.
- Provides synchronized baseline-LQ survival, final-survival, and additional-reduction panels.
- Provides baseline/final survival, no-sink/anatomical-sink, physical/effect-equivalent, and absolute-difference comparison layouts.
- Records the anatomical vascular-sink scenario as unavailable because this release accepts neither vessel geometry nor an uptake model.
- Uses one complete-volume absolute colour scale across orthogonal views. Case-relative scaling is explicitly exploratory.
- Displays a numerical colour bar, selected-voxel marker, and the complete dose-to-survival calculation chain.
- Replaces relative percentile shells with absolute 2.5%, 5%, 10%, and 20% model-consequence surfaces.
- Reports GTV, vertex, valley, peri-GTV 0–5 mm, peri-GTV 5–10 mm, and configured-OAR regional exposure and consequence.
- Exports full scalar VTI and absolute consequence VTP, PLY, GLB, 3MF, and STL geometry with versioned provenance.

## Scientific preservation

- Physical absorbed dose is never modified.
- Layer 2.2 node, edge, and midpoint-sphere definitions remain unchanged.
- The no-vascular PDE and zero-uptake contract remain unchanged.
- Existing Layer 3.2 archives remain readable through presentation-layer compatibility derivation.
- New Layer 3.2 runs use the versioned v2 result and field schemas.

## Explicit exclusions

- No toxicity, clinical-risk, TCP, NTCP, OAR-compliance, or patient-outcome prediction is produced.
- `H` is dimensionless and is not physical dose or measured concentration.
- CT pixels are not stored in the Layer 3.2 artifact; the viewer displays validated structure boundaries without inventing a CT background.
