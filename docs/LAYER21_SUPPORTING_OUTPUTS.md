# Layer 2.1 supporting outputs v2

Layer 2.1 retains the six locked harmonised metrics. The v2 supporting service adds physical-dose QA, geometry, provenance, and presentation records around the validated Layer 1 handoff. It does not change the six locked formulas or their records.

The `supporting_outputs` object contains:

- `per_vertex_qa`: vertex ID, V95 relative to RxH, Dmean, D95, Dmax, and dose-sampled volume;
- `vertex_analysis`: explicit-versus-derived source, deterministic count, individual mask hashes, and aggregate-versus-individual mask consistency;
- `high_dose_coverage_context`: covered VTVH volume, 95% RxH threshold, vertex count, and source structure names;
- `high_dose_volume_fraction_context`: VTVH and GTV volumes on one common basis, dose-sampled fraction, and VTVH outside GTV;
- `peak_valley_dose_context`: sampled volumes, voxel counts, D50, mean-dose normalisation, explicit valley source, and inherited overlap/outside-GTV warnings;
- `ratio_context`: stored Dmean(VTVH) numerator, Dmean(VTVL) denominator, formula, and display expression;
- `integrity_and_interpretability_qa`: Layer 1 and mask archive hashes, individual mask hashes, RTDOSE hash/UID, native dose-grid geometry, dose/treatment labels, prescription provenance and confirmation, inherited Layer 1 warnings, and calculation/interpretation states;
- `protocol_native_endpoint_status` and stored `protocol_native_metrics`;
- `oar_vertex_geometry`: optional descriptive OAR-to-VTVH geometry records.

## Vertex cases

When individual `VTV_H_individual` structures are mapped, per-vertex QA uses those validated RTSTRUCT masks and records `explicit_rtstruct_vertices`.

When individual structures are absent, the locked Layer 2.1 algorithm deterministically separates the aggregate `VTV_H` mask into connected components and records `connected_components_derived_from_aggregate_vtv_h`.

Both pathways calculate physical Dmean, D95, Dmax, and volume without a prescription. A valid `Rx_H` is required only for V95. If `Rx_H` is absent, each physical QA record remains available, while `v95_rxh_pct` is null and `v95_rxh_applicability` is `not_assessed`.

When explicit individual masks and aggregate VTVH are both supplied, ASCEND reports their symmetric-difference volume and a PASS/WARN consistency state. It does not silently repair inconsistent masks. When individual masks are absent, connected components are labelled deterministically as `VTVH_CC_01`, `VTVH_CC_02`, and so on.

## Optional OAR–vertex geometry

OAR entries are configured in **Structure-role mapping → Optional OAR–vertex geometry (JSON)**:

```json
[
  {"name": "Heart", "classification": "containing_organ"},
  {"name": "SpinalCord", "classification": "separate_critical_oar"}
]
```

Allowed classifications are `containing_organ`, `target_excluded_oar`, and `separate_critical_oar`. Each name must resolve uniquely to an explicit, non-empty Layer 1-validated RTSTRUCT mask. ASCEND never synthesises a target-excluded mask.

Each record reports OAR name/classification/volume, aggregate VTVH minimum surface distance, overlap volume and percentages, nearest vertex ID/distance, and per-vertex geometry. Distances use the native physical dose-grid spacing. Overlap with a containing organ is descriptive.

This module performs no OAR dose compliance or clinical pass/fail assessment. Conventional OAR Dmean, Dmax, D0.035cc, D2cc, Vx, and protocol constraint assessment remain Layer 1 responsibilities.

## Protocol endpoints

Optional endpoints are configured through structured role, endpoint-type, and value controls in **Case configuration → Protocol endpoints**. No JSON entry is required. An empty list is recorded as `not_configured`, not as a calculation failure.

Supported endpoint forms are:

```json
[
  {"id": "gtv_d95", "role": "GTV", "kind": "d_percent", "value": 95},
  {"id": "gtv_v10gy", "role": "GTV", "kind": "coverage_absolute_gy", "value": 10},
  {"id": "vtvh_v95_rxh", "role": "VTV_H", "kind": "coverage_relative_rx", "value": 0.95}
]
```

Valid roles are `GTV`, `T_L`, `VTV_H`, and `VTV_L`. Endpoint identifiers must be unique. Invalid kinds, roles, missing values, non-positive values, and D-percent values above 100 are rejected before calculation.

Existing stored Layer 2.1 results can still be opened. Rerunning Layer 2.1 is required to persist v2 per-vertex physical QA, integrity data, and OAR geometry because those outputs need the validated native masks and dose array.
