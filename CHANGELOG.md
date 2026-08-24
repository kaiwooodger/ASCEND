# Changelog

All notable ASCEND changes are recorded here. Releases follow immutable Git tags; retrospective analyses must record the exact tag and commit.

## [1.3.5] — 2026-08-24

### Added

- Spatial MLQ-Poisson TCP research branch with explicit gates and provenance.
- Optional Layer 3.2 execution switch, disabled by default.
- Git-commit identity in shared provenance and canonical exports.
- Formal validation contracts, export schema, retrospective protocol, CI workflows, Codespaces configuration, and release packaging.

### Validation

- Physical workflow validated through Layer 2.2.
- Layers 3.1A–D computationally verified but not clinically validated.
- Complete pre-freeze suite: 210 tests passed before repository-governance tests were added.

### Safety

- Clinical data, case state, derived arrays, meshes, logs, and screenshots are excluded from Git.
- Layer 3.2 research outputs are excluded from calculation and export unless explicitly enabled.
