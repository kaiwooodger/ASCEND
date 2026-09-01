# ASCEND 1.4.1

Release identifier: `ASCEND-1.4.1-L3.1-FRACTION-EVENT-RADIOBIOLOGY-20260820`

## Scope

ASCEND 1.4.1 restores Layer 3.1 from an intentional blank baseline as a gated, fraction-resolved research-radiobiology layer. Layers 1, 2.1, and 2.2 remain the immutable upstream physical evidence source.

## Implemented Layer 3.1 architecture

- Shared `FractionEvent` reconstruction with explicit temporal order, component grouping, dose/plan identities, validated geometry, registration state, delivery time, hashes, and provenance.
- Same-fraction integrated components are summed physically before nonlinear transformation. Sequential components remain separate biological events.
- Gate 0 upstream validity, Gate 1 fraction history, Gate 2 spatial correspondence, Gate 3 tissue parameters, Gate 4 delivery time, Gate 5 EUD inversion, and Gate 6 TR comparator applicability.
- 3.1A authoritative voxelwise conventional-LQ spatial BED and EQD2 fields with identity-bound tissue parameters, post-transform ROI summaries, and a non-switching high-dose model-domain warning mask.
- 3.1B Guerrero–Li modified-LQ course effect, mean direct tumour surviving fraction, bounded survival-equivalent EUD inversion, C1–C3 sensitivity scenarios, and H/V/O regional survivor decomposition.
- 3.1C gated modelled therapeutic ratio, separate N1–N3 normal-cell scenarios, explicit comparator evidence, and C1–C3 × N1–N3 matrix.
- Deterministic NPZ field storage, JSON/CSV export, source and mask hashes, and independent calculation/display provenance.

## Workstation

The native Qt page is ordered as:

1. Overview / gates and structured parameter configuration.
2. 3.1A Spatial BED / EQD2.
3. 3.1B Tumour survival / EUD.
4. 3.1C Therapeutic ratio.
5. Provenance / validation.

The field viewer is embedded in the ASCEND Layer 3.1 page. One treatment-planning workspace presents transverse, sagittal, coronal, and 3D biological/CAD panes simultaneously. Endpoint, colour range, anatomy visibility, crosshair, zoom, pan, rotation, and fit state are linked across all panes. Detailed CAD display, geometry, opacity, and export settings remain available in an optional non-modal control dialog. The former top-level viewer window and separate 2D/CAD tabs are removed. Taubin smoothing is display-only; it never enters a scientific service.

Selecting the Map tab activates a focused embedded layout: duplicated page title, scope, action, and status rows collapse while the viewer retains a 600-pixel minimum height. This gives each pane materially more vertical image area and preserves the ASCEND window boundary.

The Layer 3.1 CAD pane defaults to three orthogonal slices of the full stored tumour biological-effect field. The Step 15 regional page contains the visible 100% residual-survival contribution figure for vertices, valleys, and remaining GTV; selecting a segment focuses the same validated mask in the unified viewer.

Layer 3.2 synchronized comparison maps use the complete available tab area with zero page padding, compact legends, a narrow interpretation rail, and no white graph background.

## Scientific status

Layer 3.1 is computationally verified research software. It is not clinically calibrated or clinically validated. Its outputs are not TCP, NTCP, toxicity probabilities, patient-specific radiosensitivity estimates, or clinical recommendations. Published independent GRID/LATTICE reproductions remain required before any stronger validation claim.

The 2026-08-27 revision audit compared the frozen synthetic reference case under ASCEND 1.3.5, 1.4.0, and 1.4.1. All three produced mean MLQ SF `0.11482093801025538` and survival-equivalent EUD `5.000769465414592 Gy`, with zero revision-to-revision difference.

## Workstation maintenance update

- Biological maps now identify their category, equation, units, complete-volume colour range, and plain-language interpretation.
- Layer 2.2, Layer 3.1, and Layer 3.2 visual workspaces have explicit zoom, pan, rotation, orientation, and fit controls where geometrically meaningful.
- Layer 3.1 CAD extraction is deferred until the embedded four-pane viewer is loaded, debounced, ROI-bounding-box cropped, disk verified, and reused from memory during the session.
- The macOS CAD path defaults to a high-contrast Phong surface; scalar-aware vertex colour and VTP export remain available.
- C1–C3 and N1–N3 scenario values are preset-driven. Named-source and parameter-set provenance is read-only for registered presets.
- N1–N3 does not silently inherit tumour kinetics. Normal-cell delta and repair half-time remain incomplete until a defined normal-cell reproduction preset or explicit sourced custom values are selected.
- The Layer 3.1A LQ high-dose criterion defaults to not configured and remains an operational warning only. It never changes the formalism.
- Therapeutic-ratio comparator controls are disabled by default; sequential mixed-fraction courses remain not applicable without a protocol-defined comparator.

## Launch

```bash
cd /path/to/ASCEND_PROJECT
python3 run_ascend.py
```
