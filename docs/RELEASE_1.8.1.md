# ASCEND 1.8.1

Release identifier: `ASCEND-1.8.1-DVH-VERIFIED-DROPDOWN-PREFILL-20260910`

ASCEND 1.8.1 corrects the configuration-page source used for downstream ROI dropdowns.

## Corrected behaviour

- The optional Layer 2.1 OAR geometry selector is populated from the current TPS-DVH-verified RTSTRUCT identities as soon as valid DVHs are imported.
- The Layer 3.1C analytical OAR selector uses the same verified identity set.
- The Layer 3.1 alpha/beta assignment selector uses the same verified identity set.
- RTSTRUCT contours without an imported, valid D2%/D95% DVH remain absent from every affected selector.
- A placeholder remains selected until the user makes an explicit OAR or tissue-parameter assignment.

## Calculation gate

Dropdown eligibility and calculation readiness remain separate controls. An imported valid TPS DVH makes an RTSTRUCT identity available for configuration. Layer 2 and Layer 3 execution still require that identity to resolve to its current rasterised Layer 1 mask. Missing or stale masks continue to block the affected calculation.

## Scientific scope

This patch changes configuration presentation only. It does not modify contour interpretation, mask generation, dose sampling, physical metrics, radiobiological equations, validation thresholds, or clinical claims.

## Verification

- Static quality and test collection
- Python 3.10 compatibility tests
- Python 3.11 and 3.12 test matrix on Ubuntu, Windows, and macOS
- Regression coverage for DVH-verified dropdown population without a current Layer 1 result
