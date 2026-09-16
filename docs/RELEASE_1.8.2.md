# ASCEND 1.8.2

Release identifier: `ASCEND-1.8.2-SOURCED-MLQ-CONTROLS-20260914`

ASCEND 1.8.2 makes the Layer 3.1 MLQ parameter and delivery-time sources explicit, auditable, and user-selectable. It also incorporates the resilient PDF export correction developed after 1.8.1.

## MLQ scenario values

The registered C1, C2, and N1-N3 values reproduce Table 2 of Zhang et al., *Photon GRID Radiation Therapy: A Physics and Dosimetry White Paper*, Radiation Research 2020, DOI `10.1667/RADE-20-00047.1`.

The 2020 table does not define C3. ASCEND retains C3 alpha `0.149 Gy^-1`, beta `0.0149 Gy^-2`, and alpha/beta `10 Gy` from Zhang et al., *A Dosimetric Parameter Reference Look-Up Table for GRID Collimator-Based Spatially Fractionated Radiation Therapy*, Cancers 2022, DOI `10.3390/cancers14041037`. It is stored with separate provenance and is not represented as a 2020 white-paper value.

LQ SF2 is derived deterministically as `exp(-2 alpha - 4 beta)`. The interface does not permit an independently inconsistent SF2 or alpha/beta ratio.

## Manual tumour override

Selecting C1, C2, or C3 permits an explicit manual alpha and beta override. The interface derives alpha/beta and LQ SF2 from those inputs. A source or exploratory rationale is mandatory. Stored results identify the named scenario, overridden scope, entered parameters, derived fields, source, and parameter hash.

## Delivery-time source

Each MLQ tissue editor provides two mutually exclusive sources:

- Manual entry stores the entered value and selected time unit.
- RTPLAN control points reads the current plan's control-point meterset interval integration, converts seconds per fraction into the configured model time unit, locks the value field, and stores the plan UID and calculation evidence.

RTPLAN mode fails closed when control-point timing cannot be calculated. ASCEND does not silently reuse a manual value.

## PDF export

Complete BED and EQD2 histogram bins are emitted as repeat-header rows that can split across pages. Other nested records have bounded human-readable previews while complete values remain in authoritative JSON and CSV exports.

## Validation scope

The physical workflow remains validated through Layer 2.2. Layers 3.1A-D remain computationally verified and not clinically validated. Parameter-set corrections change research-model outputs and do not constitute clinical calibration.

### Layer 2.2 bounded anisotropic-grid extension

Layer 2.2 accepts regular native RTDOSE grids when every axis spacing is no greater than 2.0 mm. This includes grids such as 1.5 x 2.0 x 2.0 mm. Sampling remains on native RTDOSE voxels without interpolation, and distances, centroids, sphere membership, support volumes, and structure volumes use the physical per-axis spacing.

The locked validation evidence remains limited to isotropic 1 mm and 2 mm RTDOSE. An accepted anisotropic result is therefore stored as `completed_with_warnings`, uses the grid classification `extended_grid_at_or_below_2mm_per_axis`, and carries the warning `anisotropic_grid_outside_original_layer2_2_validation_scope`. Any grid with an axis spacing above 2.0 mm remains `outside_validated_scope`.

### RTSTRUCT referenced-plane rounding

RTSTRUCT contour coordinates within 0.1 mm of their referenced planning-image plane are accepted as DICOM decimal-string export rounding. This tolerance is isolated from the stricter RTDOSE and planning-image geometry tolerances. Larger offsets remain blocked and report both the referenced-plane offset and the nearest selected image SOP Instance UID and offset.
