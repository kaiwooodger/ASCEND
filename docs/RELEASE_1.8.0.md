# ASCEND 1.8.0

Release identifier: `ASCEND-1.8.0-TPS-DVH-GATED-ROI-ELIGIBILITY-20260909`

ASCEND 1.8.0 makes imported treatment-planning-system DVHs the sole authority for RTSTRUCT ROI eligibility.

## Eligibility contract

- Every imported structure DVH must contain valid D2% and D95% dose endpoints. ASCEND reports `TPS_DVH_REQUIRED_ENDPOINTS` when either endpoint is absent or invalid.
- Each accepted DVH is bound to one RTSTRUCT ROI through explicit RTSTRUCT SOP Instance UID and ROI number, or through one unique normalised structure name when those identity fields are absent.
- Layer 1 rasterises every and only the DVH-verified ROI identities. An RTSTRUCT with more contours than imported DVHs does not expose or rasterise the unmatched contours.
- Stored verification evidence is rechecked against the DVH source and selected RTSTRUCT before Layer 1 calculation. Changed evidence blocks calculation until configuration is saved again.

## Workstation behavior

- The imported DVH prefills the Layer 1 rasterisation scope and supported protocol-native endpoints.
- RTSTRUCT-only contours are removed from rasterisation, validation, OAR, and Layer 3.1 menus.
- Layer 3.1 alpha/beta parameters can be assigned only to DVH-verified ROIs that were rasterised successfully.
- Downstream services independently reject unverified ROI inventory entries instead of trusting presentation-layer filtering.

## Validation evidence

- Regression coverage includes missing D2/D95 rejection, exact eight-of-n contour binding and rasterisation scope, Layer 3.1 assignment rejection for unverified identities, GUI dropdown filtering, automatic DVH prefilling, and stale-verification detection.
- The locked physical and radiobiological scientific formulae are unchanged.
