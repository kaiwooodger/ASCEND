# ASCEND 1.3.5 release freeze

Freeze date: 2026-08-24 Australia/Sydney.

## Release identity

- Version: `1.3.5`
- Physical validation scope: validated through Layer 2.2
- Layer 3.1: computationally verified research radiobiology; not clinically validated
- Layer 3.2: optional non-local research model; disabled by default and excluded from calculation and export until explicitly enabled
- Clinical use: false

## Freeze verification

- Complete regression suite after repository-governance contracts: `214 passed`
- Python bytecode compilation: passed
- Locked Layer 1, Layer 2.1, and Layer 2.2 source-integrity checks: included in the passing suite
- Prospective Git source set: 226 files; no file exceeded 10 MB
- Credential/private-key scan: no detected credential material
- Publishable-tree scan: no DICOM, NumPy dose/mask archive, or machine-specific user-home path
- Runtime data: `runs/` and `test_runs/` remain local and are excluded by `.gitignore`

## Publication controls

- Git default branch: `main`
- GitHub Actions executes compilation and the complete regression suite on pushes and pull requests
- Named required checks cover core, DICOM, Layers 1–3.1, TCP, export schema, provenance, synthetic formal validation, lint, and typing
- Codespaces installs the declared test and quality dependencies into a clean Python 3.11 environment
- Package metadata includes the Qt icon, localhost web assets, and frozen legacy JSON configuration resources
- `.gitignore` blocks case state, DICOM, arrays, meshes, logs, crash reports, environments, caches, credentials, and build outputs
- `SECURITY.md` defines the medical-data publication boundary

No scientific calculation source was modified during repository cleanup.
