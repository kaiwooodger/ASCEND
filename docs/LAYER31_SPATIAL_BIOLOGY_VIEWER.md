# Layer 3.1 Spatial Biology Viewer

The viewer is a read-only consumer of hash-verified Layer 3.1 voxel fields. It never calculates BED, EQD2, MLQ survival, EUD, TCP, or another biological endpoint.

## Authoritative data flow

```text
RTDOSE and validated masks
  -> Layer 3.1 scientific services
  -> stored physical-dose, s-BED, s-EQD2 and MLQ arrays
  -> immutable BiologicalVolume in DICOM patient LPS
  -> slice, PyVista volume, isosurface, or VTK mesh-sampling display
```

`BiologicalVolume.values` uses NumPy `z,y,x` order. `VolumeGeometry` owns the only `voxel z,y,x <-> patient x,y,z` transform. Its affine columns are patient-space x, y and z voxel axes. The PyVista adapter reverses dimensions to x, y, z and flattens the source in C order because VTK point ids increase x-fastest. No unexplained transpose or axis swap exists in the GUI.

The volume, masks, geometry arrays, treatment-component identifiers, and metadata are defensively copied and made read-only at the renderer boundary. Display settings cannot mutate them.

## Endpoints

The renderer accepts explicit enum values:

- Physical dose, Gy.
- Spatial BED, Gy with the stored ASCEND tissue label retained.
- Spatial EQD2, Gy with the stored ASCEND tissue label retained.
- MLQ surviving fraction.
- MLQ biological effect, `-ln(SF)`.

Tumour EUD remains a tumour-level summary. It is not represented as a spatial endpoint. When `SF` must be clipped to produce a logarithmic display transform, Layer 3.1 records `MLQ_SF_NUMERICAL_CLIPPING`, the affected voxel count, and the numerical floor. Raw surviving fraction is retained.

## Rendering modes

- **Surface:** VTK samples the connected biological volume at original CAD/STL vertices. Display geometry may be smoothed only after sampling coordinates are retained.
- **Volume:** PyVista volume-renders the selected authoritative mask with an endpoint-specific opacity transfer function.
- **Isosurface:** VTK contours internal biological shells at declared absolute or display-percentile values.
- **Slice:** axial, coronal, and sagittal views query authoritative arrays.
- **Combined:** translucent anatomy, internal volume, biological shells, and stored Layer 2.2 vertex centres share one patient-space scene.

Percentage thresholds are labelled as visualisation thresholds, not clinical thresholds. Raw SF uses reversed colour/opacity semantics because lower SF means stronger calculated kill. `-ln(SF)` is the preferred effect-intensity view.

The Qt widget uses off-screen PyVista/VTK rendering and paints the resulting scene through PySide6. This avoids the macOS Qt3D/Metal failure path while preserving VTK volume rendering, camera persistence, point picking, and platform parity.

## ASCEND 1.4.0 workstation behaviour

Layer 3.1 uses three responsive workflow stages: maps and controls, whole-tumour result, and regional explanation. Fixed left/right output columns no longer force the parent workstation page wider than the screen. The map stage uses a resizable analysis/map splitter; long analysis and CAD control groups scroll vertically inside their own panels without creating a page-level horizontal scrollbar.

The navigation toolbar is shared by the slice and CAD tabs. Orientation selects the corresponding CAD camera and slice focus. Zoom, rotation, and fit actions are applied to every 2D plane and the CAD camera, so switching tabs does not expose a second control convention. The selected biological endpoint and anatomy visibility are likewise shared by both renderers.

CAD scalar-bar title and value labels use a near-white foreground against the dark-blue viewport. Automatic VTK scalar bars are disabled so the renderer creates exactly one explicitly styled legend.

Continuous CAD interaction is coalesced to approximately 30 display updates per second. Dragging and wheel interaction use a bounded, reduced-resolution preview; release produces a full-resolution frame. Mesh rebuilds and opacity changes are debounced, matching in-flight scene requests are reused, and an unchanged immutable biological volume is not reconverted on each presentation update. Volume shading is disabled because the software-rendered off-screen path benefits materially from the lower GPU/CPU cost.

## Region and invalid-data handling

Volume rendering applies the selected GTV, vertex, valley, OAR, custom ROI, or all-valid-tissue mask before actor creation. Outside values are `NaN` in the derived masked grid. The VTK mapper alone receives a temporary display buffer mapping unavailable voxels to zero opacity. That buffer is never reported, exported as science, or returned by the probe.

Surface sampling uses `PolyData.sample(ImageData)` and exposes:

- total vertices;
- valid and invalid biological samples;
- valid fraction;
- `vtkValidPointMask` and `ascendBiologicalSampleValid`;
- `NaN` for invalid scalar values.

Rendering is blocked below the configurable 0.98 valid-sample fraction. Zero is never an invalid marker.

## Spatial gates

Before rendering, ASCEND validates finite origin, positive spacing, orthonormal orientation, invertible and internally consistent affine, volume and mask shapes, endpoint metadata, finite values in the valid mask, patient-space identity, mesh/volume overlap, and surface sample validity.

Geometry failure is fail-closed and includes volume bounds, mesh bounds and centroid, origin, spacing, dimensions, and coordinate-system identifier. ASCEND never translates or scales a mesh to make it appear registered.

## Colour and opacity

The colour-scale manager stores true minimum/maximum separately from robust display limits. Defaults are the valid selected region's P02 and P98. Absolute and locked-comparison scales are supported. A locked scale survives camera movement, slice changes, region changes, actor visibility changes, and clipping.

Dose, BED, EQD2, effect, and surviving fraction have separate colour and opacity semantics. Outside-mask opacity is always zero. Scalar smoothing is absent. Any anatomical surface smoothing remains display-only.

## Quantitative views

The probe maps a patient coordinate through the affine, then reads the authoritative voxel from every loaded endpoint. It reports patient and voxel coordinates, physical dose, s-BED, s-EQD2, MLQ SF, MLQ effect, tumour/OAR region, vertex membership, and valley membership. Unavailable values are `N/A`, never zero.

Region statistics include valid count, minimum, maximum, mean, median, standard deviation, P05, P25, P75, and P95. Tumour summaries also include vertex and valley mean/median and descriptive contrast. These values have no additional clinical interpretation.

## Validation coverage

Automated tests cover:

- rotated, reflected, and anisotropic geometry round trips;
- NumPy/VTK x, y, and z scalar ordering;
- analytic patient-space gradient sampling;
- mask-to-NaN behaviour;
- mesh-overlap failure without implicit registration;
- raw authoritative slice/probe/statistics agreement;
- inverse MLQ SF opacity semantics;
- stable locked colour scales;
- VTK volume and combined actors;
- an internal spherical hotspot whose external tumour surface remains constant;
- internal isosurface centre and topology.

The pre-implementation repository baseline was 215 passing tests and 28 passing subtests. Scientific Layer 3.1A and 3.1B equations were not modified by this rendering work.

## Export and provenance

Anatomical STL contains geometry only. Scalar VTP retains quantitative surface samples and validity arrays. PNG exports are display artefacts. Every canonical volume retains field identity, model metadata, treatment-component names, source-dose UIDs, coordinate system, and stored calculation provenance supplied by Layer 3.1.
