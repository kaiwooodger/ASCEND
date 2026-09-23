# ASCEND 1.8.4

Release identifier: `ASCEND-1.8.4-REGIONAL-SURVIVAL-REPORT-20260923`

## Corrected

- Added an explicit Layer 3.1B PDF table for the stored vertex, valley, and remaining-tumour survival decomposition.
- Reported each region's voxel count, tumour-volume fraction, mean surviving fraction, and residual-survivor contribution.
- Reported the contribution sum and reconciliation residual so the three-region partition can be checked from the PDF.

## Scientific scope

The report presents stored ASCEND results without recalculation. Regional mean surviving fractions are model outputs. Survivor contributions are each region's share of the modelled surviving tumour-cell burden and sum to 100% subject to numerical precision. These quantities are not observed tumour outcomes or clinical-effect estimates.

## Verification

The targeted PDF, release-identity, provenance, and browser-asset tests passed: 10 tests. Ruff passed for the changed Python files. A synthetic two-page report was rendered to PNG and visually inspected; the regional table was legible with no clipping or overlap. Source and wheel distributions built successfully as 1.8.4 with build isolation disabled because network access was unavailable. The unrestricted suite reached native PyVista rendering tests and the VTK process crashed under the restricted graphics environment; this is the documented class of test that requires native macOS graphics access.
