# ASCEND 1.8.1 Dropdown Prefill Correction Report

Date: 2026-09-10  
Affected interface: Structure-role mapping and Layer 3.1 configuration

## Reported symptom

The TPS-DVH-verified Layer 1 rasterisation table contained imported structures, but the optional Layer 2.1 OAR geometry dropdown and Layer 3.1C analytical OAR dropdown displayed only their placeholder. The Layer 3.1 alpha/beta dropdown used the same failing population path.

## Root cause

ASCEND 1.8.0 stored the correct authoritative eligibility set in `dvh_verified_rois`, but those three downstream dropdowns were populated only from a current Layer 1 result inventory. A newly imported DVH invalidates dependent results, and older result inventories may not contain the 1.8.0 verification field. In either state, the interface had verified identities available but added no downstream options.

## Correction

All affected dropdowns now derive their choices directly from the current `dvh_verified_rois` records bound to the selected RTSTRUCT SOP Instance UID and ROI number. The same records define the complete Layer 1 rasterisation scope.

ASCEND does not automatically select an OAR or assign an alpha/beta value. Each dropdown opens on a placeholder, while every eligible DVH-verified identity is available as a choice. A previously selected identity is retained only if it remains in the verified set.

## Safety controls retained

- No RTSTRUCT-only contour is presented downstream.
- Every eligible ROI must have an imported TPS DVH with valid D2% and D95%.
- Identity binding remains RTSTRUCT SOP Instance UID plus ROI number.
- Layer 2 and Layer 3 calculations still require the selected identity to resolve to a current Layer 1 rasterised mask.
- Invalid, missing, changed, or unmatched DVH evidence continues to fail closed.

## Regression coverage

The GUI regression test constructs an RTSTRUCT with both verified and unverified contours, provides verified DVH identity records without a current Layer 1 result, and confirms that:

1. Layer 2.1, Layer 3.1C, and alpha/beta dropdowns contain only verified identities.
2. The unverified RTSTRUCT contour is absent.
3. The placeholder remains selected.
4. Selector choices persist across a configuration refresh.

## Scientific impact

No scientific calculation changed. This is a state-source and interface correction that makes the existing TPS-DVH eligibility contract visible at the required configuration points.
