# OAR–Vertex 0.0 mm Audit

## Finding

Layer 2.1 does not calculate vertex diameter. The affected field is
`nearest_vertex_distance_mm`, defined as minimum native-grid mask-surface
separation. A value of `0.0 mm` means the OAR/internal-structure mask and vertex
mask overlap. It does not mean that the vertex diameter is zero.

The geometry calculation first tests mask intersection. If any validated native
voxels are shared, minimum separation is exactly zero by definition. Positive
values are reported only when masks are spatially separated.

## PHPROLRT01 evidence

Stored run inspected: `ASCEND_L2_1_20260813_050527_873831`.

| Structure | Configured classification | Nearest separation (mm) | Aggregate overlap (cc) | Vertices overlapping | Vertices separated |
|---|---|---:|---:|---:|---:|
| BODY | separate critical OAR | 0.0 | 12.554 | 8 | 0 |
| Lung_L | separate critical OAR | 0.0 | 10.482 | 8 | 0 |
| HighDensityCTV | separate critical OAR | 0.0 | 12.496 | 8 | 0 |
| Lung_subHD | separate critical OAR | 0.0 | 0.066 | 1 | 7 |
| Heart | separate critical OAR | 22.672 | 0.0 | 0 | 8 |

BODY is a containing structure, and HighDensityCTV is target-related in this
dataset. Their `separate_critical_oar` classification is inconsistent with the
observed geometry. The result remains descriptive and makes no compliance or
clinical failure determination.

## Implemented audit fields

New Layer 2.1 OAR geometry records contain:

- `aggregate_vtvh_spatial_relationship`;
- `nearest_vertex_spatial_relationship`;
- `nearest_vertex_zero_distance_reason`;
- per-vertex `spatial_relationship`;
- per-vertex `zero_distance_reason`;
- `geometry_audit.vertex_diameter_calculated`, always `false`;
- counts of overlapping and separated vertices;
- an information finding when zero distance is caused by overlap;
- a warning when a structure configured as `separate_critical_oar` overlaps
  VTVH.

The GUI labels were changed from ambiguous “distance” wording to “minimum
separation” and now include a per-vertex audit table.

## Scope

This audit does not calculate vertex diameter, OAR dose compliance, or clinical
pass/fail. Existing conventional OAR dose constraints remain outside this
optional descriptive geometry module.
