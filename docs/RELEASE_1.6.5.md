# ASCEND 1.6.5

Release identifier: `ASCEND-1.6.5-IDENTITY-BOUND-MASK-BOUNDARIES-20260908`

ASCEND 1.6.5 enforces the anatomical-mask ownership boundary between Layer 1 and downstream analysis.

## Configuration contract

The former cross-layer `oar_structures` setting is replaced by three independent identity lists:

- `layer1_rasterisation_rois`: additional RTSTRUCT identities whose masks Layer 1 creates and archives.
- `layer21_oar_geometry_rois`: current Layer 1 masks included in optional descriptive OAR geometry.
- `layer31c_oar_rois`: current Layer 1 masks included in Layer 3.1C normal-tissue analysis.

Each identity is authoritative only as the pair `rtstruct_sop_instance_uid` and `roi_number`. Names are presentation metadata obtained from Layer 1 inventory records.

## Layer 3.1C gates

Layer 3.1C performs exact identity lookup against the current Layer 1 inventory and accepts only records with `rasterisation_status == "rasterised"`. It does not match `name`, `display_name`, or `canonical_mapping` from configuration.

- No configured Layer 3.1C OAR produces `NOT_ASSESSED` with `NO_LAYER31C_OAR_CONFIGURED`.
- Any missing or unrasterised selected identity blocks the complete 3.1C branch with `ROI_REQUIRES_LAYER1_RASTERISATION`.
- A partially resolved selection never calculates on the resolved subset.
- The GTV is never substituted for an absent normal-tissue selection.

## Invalidation contract

| Configuration change | Invalidated results |
| --- | --- |
| Layer 1 rasterisation ROI set | Layer 1, Layer 2.1, Layer 2.2, Layer 3.1, Layer 3.2 |
| Layer 2.1 OAR geometry selection | Layer 2.1 |
| Layer 3.1C analytical OAR selection | Layer 3.1, Layer 3.2 |

## Compatibility

`oar_structures` remains accepted only for migration of cases saved before 1.6.5. Scientific services do not consume it. Identity-bound legacy records can be separated safely; legacy name-only records are not promoted into Layer 3.1C.

## Verification

Regression coverage includes wrong UID, wrong ROI number, correct name with wrong identity, absent Layer 1 mask, no selected OAR, partial multi-OAR resolution, selective invalidation, and expansion of the Layer 1 rasterisation set followed by a successful rerun.
