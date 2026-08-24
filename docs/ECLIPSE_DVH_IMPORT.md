# Eclipse DVH reference import

ASCEND Layer 1 accepts either the existing normalized TPS metrics CSV, one Eclipse cumulative-DVH text export, or a folder containing Eclipse `.txt` exports.

The Eclipse path is configured under **Case configuration → Eclipse DVH reference (CSV, TXT, or folder)**. Select **Browse folder** when Eclipse produced multiple files for the same patient and plan.

## Identity and ambiguity gates

Import stops before validation when:

- the Eclipse Patient ID differs from the ASCEND DICOM Patient ID;
- the selected files contain more than one patient, course, plan, or total-dose normalization;
- the Eclipse plan label differs from the selected RTPLAN label;
- required patient, plan, total-dose, or structure sections are absent;
- redundant preferred exports disagree beyond 0.001 Gy or 0.01 cc;
- no Eclipse structure maps to the configured GTV role.

ASCEND does not infer structure meaning from export filenames. It reads every `Structure:` section and applies the explicit ASCEND structure-role configuration. Filename labels such as `CTV`, `vertices`, or `valleys` are not treated as evidence.

## Compatibility contract

Compatibility is case-independent but format-bounded. The importer supports arbitrary patient IDs, plan labels, and structure names when the file is an Eclipse text DVH export using English field labels. Supported variations include:

- one structure per file or all structures in every file;
- UTF-8 with or without BOM, UTF-16, and Windows-1252 text;
- decimal-point and decimal-comma numbers, including common thousands separators;
- `Gy`, `cGy`, `cm³`, `cc`, and `mL` units;
- direct dose or relative dose normalized through the exported total dose;
- absolute-dose-first or relative-dose-first cumulative curves;
- two-column dose/volume and three-column dose/relative-dose/volume curves;
- common Eclipse label variants for patient, plan, course, total dose, volume, minimum, maximum, and mean dose.

Binary Eclipse objects, screenshots, PDFs, differential DVHs, localized non-English field labels, and manually edited reports without stable headers are outside this contract. They are rejected rather than silently guessed. A new export variant requires a de-identified fixture and parser regression test before it enters the validated import contract.

## Metric normalization

The importer recognizes `Volume`, `D95`, `D2`, `Min Dose`, `Max Dose`, and `Mean Dose` for every exported structure. It normalizes:

- `cm³`, `cc`, and `mL` to `cc`;
- `Gy` directly;
- `cGy` to `Gy`;
- relative dose `%` to `Gy` only through the explicit Eclipse `Total dose [Gy]` field.

When redundant reports provide both direct Gy values and relative-dose percentages, direct Gy is preferred. Relative exports remain recorded as corroborating observations. Cumulative curves are accepted in either Eclipse column order and stored in normalized dose-first form.

If the total-dose normalization is absent, direct Gy/cGy metrics remain usable. Relative-dose metrics are marked not assessed and generate a Layer 1 warning; they are never converted using RTDOSE maximum or an inferred prescription.

## Validation pathway

Configured GTV metrics are bridged into the preserved Layer 1 TPS-agreement calculation. The broader Eclipse audit compares all one-to-one validated structures for volume, D95, D2, minimum, maximum, and mean dose. A comparison is marked `NOT_ASSESSED` rather than guessed when a structure has no validated mask or multiple RTSTRUCT ROIs were combined.

The external Eclipse audit does not replace ASCEND dose reconstruction, contour rasterization, mask QA, or the Layer 1 eligibility gate. It adds independent TPS agreement evidence without changing the preserved scientific calculations.

The current provisional agreement tolerances are 3% of the Eclipse value for volume and the larger of 0.2 Gy or 2% of the Eclipse value for dose. A tolerance exceedance is reported as `WARN`; identity, ambiguity, and mapping failures stop the import.

## Output artifacts

Each Layer 1 result stores:

- `eclipse_dvh_normalized.csv`;
- `eclipse_dvh_curves.csv`;
- `eclipse_dvh_audit.csv`;
- `eclipse_dvh_import.json`;
- SHA-256 hashes for every input export and generated audit artifact.

The Layer 1 GUI exposes these records under **Eclipse DVH audit** and **Import provenance**.
