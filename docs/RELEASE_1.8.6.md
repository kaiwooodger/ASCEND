# ASCEND 1.8.6

Release identifier: `ASCEND-1.8.6-LAYER32-ALPHA-BETA-CONSISTENCY-20260924`

## Scope

Layer 3.2 now declares where its LQ alpha and beta coefficients originate. The user can inherit the current stored Layer 3.1B tumour coefficients or enter independent Layer 3.2 coefficients. The result records the resolved values, source mode, Layer 3.1 parameter identity when applicable, and the effective alpha/beta ratio.

Layer 3.2 sensitivity analysis can be disabled, reuse the complete Layer 3.1 tumour-site sensitivity contract, or use an independent Layer 3.2 range. Each sample stores alpha/beta, alpha, beta, SF2, mean GTV baseline survival, mean GTV final survival, baseline and biological effect-equivalent iPVDR medians, and signed shifts.

## Reporting

The workstation, structured JSON export, and PDF report expose the resolved Layer 3.2 alpha/beta source and sensitivity records. Sensitivity remains exploratory research output and does not represent a clinical outcome prediction.

## Compatibility

Existing saved cases load in manual Layer 3.2 mode, preserving the prior configured or default coefficients. Matching Layer 3.1 is an explicit selection.
