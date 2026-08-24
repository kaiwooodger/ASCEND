# ASCEND 1.2.0

Non-local biological-effect research-model workstation.

Validation scope: Layers 1, 2.1, 2.2, and 3.1 retain their existing validation status. Layer 3.2 is provisional, hypothesis-generating, and not clinically validated.

## Layer 3.2

- Reuses the immutable Layer 1 masks and physical geometry.
- Reuses the frozen Layer 2.2 node identities, edges, vertex masks, and 3 mm midpoint-sphere valley definition.
- Reuses the Layer 3.1 fraction-history-aware `P(x)` and `Q(x)` basis.
- Calculates a two-species ROS-like/cytokine-like diffusion, decay, and dose-emission model adapted from SFRT-MODEL1.
- Calculates cumulative non-local mediator exposure H, scaled exposure sH, the non-local survival multiplier, additional modelled survival reduction relative to LQ, final survival, and a model-derived biological effect-equivalent dose.
- Reports signed biological iPVDR shift, non-local-only iPVDR shift, valley effect shift, whole-GTV context, 0–5/5–15/15–30 mm spill shells, and configured OAR spill summaries.
- Stores physical, baseline, final effect-equivalent, delta-effect, mediator-exposure, survival-multiplier, survival-reduction, final-survival, ROS-like, cytokine-like, GTV, vertex, and OAR arrays in one hash-verified NPZ artifact.
- Provides synchronized baseline-LQ, final-survival, and additional-reduction panels with a fixed complete-volume colour scale and selected-voxel calculation chain.
- Provides DICOM-LPS 3D absolute 2.5%, 5%, 10%, and 20% consequence surfaces with anatomical overlays, clipping, opacity, orthogonal cameras, voxel probing, and an explicit model-crop boundary.
- Reports modelled regional exposure and consequence for the GTV, vertices, valley region, peri-GTV shells, and configured OARs.
- Exports full scalar VTI, coloured VTP/PLY/GLB/3MF, geometry-only threshold STL, anatomical-mask STL, and a limitations/provenance manifest.

## Explicit exclusions

- Physical absorbed dose is never changed.
- Vascular geometry and vascular uptake are not accepted inputs and are not present in the PDE.
- The model records uptake as exactly zero.
- Edge profiles are visualisation-only; graph iPVDR uses the unchanged Layer 2.2 sampling geometry.
- No TCP, NTCP, clinical OAR compliance, treatment approval, or patient-outcome prediction is produced.

## Source provenance

The model was adapted from `kaiwooodger/SFRT-MODEL1` commit `b894eeef2cb1b3359bdbe4eff8881a43a690de9b` under BSD-3-Clause. Source file hashes are stored in every Layer 3.2 result. See `THIRD_PARTY_NOTICES.md`.
