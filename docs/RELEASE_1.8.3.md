# ASCEND 1.8.3

Release identifier: `ASCEND-1.8.3-CASE-RESET-OAR-REPORT-20260918`

Both the native Qt and optional localhost browser workstation headers now provide **Reset / new case**. It clears the active case and session provenance, all reusable caches within the active case workspace, pending Eclipse references, edited configuration and mappings, results, viewer data, and export selections. Every page is reconstructed with its startup defaults and navigation returns to Import. Reset is disabled while a background operation is running; cache deletion failures are reported and the active workstation remains open.

Saved case JSON, validated/derived evidence, source DICOM/DVH files, and exported reports are preserved. Opening a saved case explicitly restores its saved evidence. Importing a new case uses a fresh workspace and cannot inherit the prior case's state or caches.

The browser reset reloads its page after the controller clears the case; browser imports also create a fresh workspace for each import.

The default PDF selection includes **Layer 3.1C therapeutic ratio and OAR EUD / SF**. This section now prints a dedicated per-OAR table containing the OAR name, classification, dose-sampled volume in cm3, mean normal-tissue surviving fraction (SF, dimensionless), normal-tissue survival-equivalent EUD in Gy, and solver/applicability state. All records are included, including zero-valued endpoints; missing values are labelled unavailable and missing-result reasons remain visible. These are stored MLQ research outputs, not NTCP or toxicity predictions. Export performs no new biological calculations.

Verification on macOS ARM64 / Python 3.12: the complete suite passed with 335 tests and 40 subtests. Ruff, targeted mypy checks, repository boundary audit, JavaScript syntax, and whitespace checks passed. Source and wheel distributions built as 1.8.3, with the new lifecycle module included. The native reset screen and a 40-OAR, three-page synthetic PDF were visually inspected; all 40 OAR records were confirmed in extracted PDF text. Existing dependency deprecation warnings remain. Native VTK tests require macOS graphics access outside the restricted filesystem sandbox.
