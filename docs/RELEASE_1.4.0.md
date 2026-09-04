# ASCEND 1.4.0

Release identifier: `ASCEND-1.4.0-RESPONSIVE-LAYER31-VIEWER-20260827`

## Scope

ASCEND 1.4.0 is a Layer 3.1 workstation usability and rendering-performance release. It does not change the authoritative physical or radiobiological calculations.

## Layer 3.1 interface

- The viewer is separated into map, whole-tumour result, and regional-explanation stages.
- Fixed-width side panels and oversized graphics minima were removed.
- Analysis and detailed CAD controls scroll vertically inside bounded panels; the containing workstation page no longer requires a horizontal scrollbar at the validated 1000×720 QA size.
- The 2D slicer and 3D CAD viewer use one linked toolbar for orientation, zoom, rotation, and fit.
- Endpoint selection, anatomy visibility, ROI focus, and colour range remain shared across the 2D and CAD presentations.
- CAD scalar-bar text is explicitly near-white against the dark-blue viewport.

## Rendering performance

- Camera interaction is coalesced to a 33 ms update interval.
- Dragging uses a bounded 55% resolution preview and restores full resolution after release.
- Mesh changes use a 140 ms debounce; opacity changes use a 120 ms debounce.
- Matching in-flight mesh work is reused and completed bundles are cached by display state.
- Immutable biological volumes are not reloaded when only scene presentation changes.
- Cross-platform off-screen volume rendering uses unshaded compositing to reduce interaction cost.

## Layer 3.1 corrective update

- The unified spatial viewer opens in a dedicated top-level window and can be reopened from the Layer 3.1 page.
- Viewer materialisation accepts scientifically identical fraction histories across NumPy runtimes when authoritative event-dose hashes match, while still rejecting changed dose arrays or provenance.
- Selecting N1–N3 in the workstation visibly selects the registered Zhang 2022 normal-cell kinetic reproduction by default; sourced custom kinetics remain available.
- Layer 3.1C evaluates normal-tissue survival over the union of validated configured OAR masks and records the exact scope used for therapeutic ratio.
- Layer 3.1C stores and exports a separate normal-tissue survival-equivalent EUD summary for each configured, validated OAR. These research values are not NTCP, toxicity predictions, constraints, or clinical recommendations.

## Validation position

- Layers 1–2.2 retain their existing validation position.
- Layers 3.1A–D remain computationally verified research models and are not clinically validated.
- No dose, BED, EQD2, MLQ survival, EUD, TCP, mask, registration, or geometry algorithm was modified.

## Launch

```bash
python run_ascend.py
```

Use the Python executable from the ASCEND virtual environment when the system `python` command is not configured.
