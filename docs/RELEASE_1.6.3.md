# ASCEND 1.6.3

Release identifier: `ASCEND-1.6.3-UNIFIED-SPATIAL-RADIOBIOLOGY-GUI-20260820`

ASCEND 1.6.3 consolidates Layer 3.1 physical dose, spatial LQ BED/EQD2,
Guerrero–Li MLQ survival, MLQ effect, biological CAD surfaces, and regional
survival interpretation into one Qt Widgets workspace. The scientific services,
stored arrays, calculation algorithms, and Layer 1 through Layer 2.2 contracts
are unchanged.

## Viewer contract

- The GUI follows a visible 17-step sequence from upstream case preparation to
  provenance/export. The result hierarchy is fixed as map, whole-tumour result,
  then regional explanation.
- The map is the primary Layer 3.1A output. Mean tumour surviving fraction and
  tumour EUD are the primary Layer 3.1B numerical outputs.
- One validated anatomy and crosshair are retained across all displayed fields.
- The preferred survival view is the display-only `−log10(SF)` transform; stored
  surviving-fraction values remain authoritative.
- Complete-volume colour ranges are used by default.
- Physical dose, LQ-reference fields, and MLQ survival/effect use distinct
  quantitative palette families while anatomy and warning masks remain outlines.
- High-dose LQ-domain warnings remain outlines and do not switch biological models.
- The 100% residual-survival contribution bar is the primary regional visual.
  It and the three stored regional cards are linked to validated tumour-region
  masks in the 2D and 3D viewers.
- C1–C3 changes are routed back through case configuration and the Layer 3.1
  scientific service before the viewer accepts a replacement field set.
- CAD smoothing affects presentation geometry only. Analysis smoothing remains
  `NONE`.
- The Layer 3.1 CAD view is a composite DICOM-patient-space scene rather than a
  single selected-ROI surface. It retains the validated GTV, vertex, valley and
  configured OAR masks as independently visible anatomical surfaces.
- Stored spatial BED and EQD2 fields can be switched on or off as mutually
  exclusive quantitative overlays on the GTV surface. Ten fixed-range surface
  bands provide a dependable macOS Qt3D rendering path; no field is recomputed
  in GUI code.
- Anatomical and overlay surfaces may use non-shrinking display smoothing. Raw
  masks, voxel fields, metrics and scientific exports remain unchanged.
- A paired-course comparison is never inferred. The comparison panel reports
  `NOT CONFIGURED` until a second hash-verified field set is available on the
  same validated geometry.

## Validation position

The physical workflow remains validated through Layer 2.2. Layer 3.1 is a
computationally verified research model and is not clinically validated. Its
survival outputs are not TCP, NTCP, toxicity, or treatment recommendations.
