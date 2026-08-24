# ASCEND anisotropic Layer 1 / Layer 2.1 validation

Overall status: **PASS**

This evidence package validates physical-coordinate reconstruction and locked Layer 2.1 metrics on regular anisotropic grids. It does not expand Layer 2.2's validated domain.

| Grid | L1 geometry | Uniform DVH | Physical gradient | L2.1 | L2.2 | Complete DICOM chain |
|---|---|---|---|---|---|---|
| 1.0x1.0x1.0 | PASS | PASS | PASS | PASS | completed | PASS |
| 1.0x1.0x2.0 | PASS | PASS | PASS | PASS | outside_validated_scope | PASS |
| 1.0x1.0x2.5 | PASS | PASS | PASS | PASS | outside_validated_scope | PASS |
| 1.0x2.0x2.5 | PASS | PASS | PASS | PASS | outside_validated_scope | PASS |
| 2.0x2.0x3.0 | PASS | PASS | PASS | PASS | outside_validated_scope | PASS |

## Interpretation

Sampled volumes are resolution-dependent and are assessed against analytic ground truth using signed relative error. Exact cross-grid voxel-volume equality is not required. Uniform and patient-coordinate dose fields must remain numerically exact at sampled voxel centres.

Supported claim: Layer 1 and Layer 2.1 are validated on regular anisotropic dose grids across the tested resolution domain. Layer 2.2 returns `outside_validated_scope` for anisotropic grids.
