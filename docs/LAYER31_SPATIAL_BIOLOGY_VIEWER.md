# Layer 3.1A Spatial Biology Viewer

The Spatial Biology Viewer is a read-only presentation layer over hash-verified Layer 3.1A voxel fields. It never calculates BED, EQD2, EUD, TCP, or other biological endpoints.

## Authoritative data flow

```text
Layer 3.1A calculation
  -> stored physical-dose, s-BED, and s-EQD2 arrays
  -> SpatialBiologyField contract in DICOM patient LPS (mm)
  -> mask-aware inward surface sampling
  -> Qt 3D scalar surface, cutaway, or isosurface
```

The 3D scalar values originate from the stored voxel arrays. They are not calculated at mesh vertices and are not projected from a 2D image. Numerical cards use stored voxelwise Layer 3.1A summaries, never the displayed surface mesh.

## Display modes

- **Biological surface map:** scalar-coloured GTV surface.
- **Biological cutaway:** an axial, sagittal, coronal, or obliquely rotated clipping plane exposes the stored interior field.
- **Biological isosurfaces:** one to four absolute or percentile thresholds from the authoritative field.

The viewer can display physical dose, s-BED, or s-EQD2. A single colour-scale controller drives the 2D planes, 3D rendering, colourbar, and inspector. Robust, full, manual, and percentile ranges are presentation settings only.

## Spatial safety gates

All fields and surfaces use DICOM patient LPS in millimetres. Before biological colouring, ASCEND records grid, ROI, and mesh bounds and validates the fraction of surface vertices that can be sampled:

- GREEN: at least 99% valid samples.
- AMBER: 95% to less than 99% valid samples.
- BLOCK: less than 95%, or no geometric overlap.

Continuous fields use trilinear interpolation constrained to the expected tissue mask. Surface samples move inward by 0.25 to 1 voxel along both normal directions. The final fallback is the nearest valid voxel within one voxel and within the same ROI. Invalid samples remain `NaN`; zero is never used as an invalid marker.

## LATTICE and anatomy context

Validated GTV, VTVH, valley, and configured OAR masks are rendered in the same scene. When a completed Layer 2.2 result exists, its stored vertex centroids and nearest-neighbour edges can be overlaid. These annotations do not alter Layer 3.1A fields.

## Interaction and performance

The scene supports rotate, pan, zoom, anatomical views, point picking, and a shared LPS crosshair across axial, sagittal, coronal, and 3D views. The inspector reads physical dose, s-BED, s-EQD2, tissue parameter, region, and declared treatment components from authoritative data.

Mesh extraction, smoothing, sampling, cutaway generation, and isosurface generation run outside the Qt GUI thread. Geometry and scalar results are cached using case-local deterministic keys. Opacity and camera changes do not rebuild scientific fields.

## Export

The viewer exports anatomical STL files and scalar-bearing VTP files. STL contains geometry only; quantitative biological scalars remain in VTP and metadata. The current quantitative 3D view can be exported as PNG.

## Explicit limits

- Component-specific and split-screen fields appear only when separate authoritative component arrays exist. ASCEND does not infer component fields from a total.
- The viewer does not perform implicit registration, dose warping, biological recalculation, or nearest-neighbour scalar fallback.
- Display smoothing affects geometry only. Scalar smoothing is off and no displayed surface is used for numerical analysis.
