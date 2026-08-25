# Synthetic reference cases

Only explicitly synthetic, non-clinical reference cases may be stored here. Fixtures must use generated UIDs, synthetic patient identifiers, and no geometry or metadata copied from a clinical export.

Large generated DICOM and array artifacts should be created during tests and discarded afterward rather than committed. Machine-readable validation summaries may be committed when they contain no patient-derived values or identifiers.

`cross_platform_expected.json` freezes the expected outputs and explicit numerical tolerances for the synthetic GitHub Actions reproducibility case. Each operating-system/Python runner generates the case independently; the comparison job checks every result against these values and against the Ubuntu/Python 3.11 result.
