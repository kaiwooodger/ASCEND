# ASCEND 1.8.5

Release identifier: `ASCEND-1.8.5-TUMOUR-ALPHA-BETA-SENSITIVITY-20260923`

## Added

- Added an optional Layer 3.1B tumour-site alpha/beta sensitivity section, disabled by default.
- Added user inputs for tumour site, minimum and maximum alpha/beta in Gy, sample count, and source or exploratory rationale.
- Added an explicit parameter-scaling choice: hold alpha constant and derive beta, or hold beta constant and derive alpha.
- Stored the derived alpha, beta, SF2, mean tumour surviving fraction, tumour EUD, and vertex/valley/remaining-tumour survival decomposition for every sampled alpha/beta value.
- Added workstation and PDF result tables plus structured and CSV export coverage.

## Scientific scope

An alpha/beta ratio alone does not uniquely identify alpha and beta. ASCEND therefore requires an explicit fixed-parameter rule and records every derived parameter set. The tumour-site label and range are user-declared exploratory inputs. The analysis does not estimate patient-specific radiosensitivity or predict clinical outcome.

## Verification

Forty-one targeted Layer 3.1, report, GUI, release-identity, provenance, and browser-asset tests passed. Ruff passed across the application and changed tests. Source and wheel distributions built successfully as 1.8.5. The enabled configuration card and a four-page synthetic PDF report were rendered and visually inspected with no clipping, overlap, or unreadable fields.
