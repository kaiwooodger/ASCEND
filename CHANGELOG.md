# Changelog

All notable ASCEND changes are recorded here. Releases follow immutable Git tags; retrospective analyses must record the exact tag and commit.

## [1.6.0] — 2026-09-03

### Changed

- Updated package, runtime, and repository release metadata to ASCEND 1.6.0.
- Aligned browser workstation branding and release-contract tests with ASCEND 1.6.0.

### Scientific scope

- No Layer 1, Layer 2.1, Layer 2.2, Layer 3.1, or Layer 3.2 calculation algorithms were changed.

## [1.4.0] — 2026-08-27

### Changed

- Reworked Layer 3.1 into responsive map, whole-tumour result, and regional-explanation stages.
- Replaced fixed-width result panels and oversized graphics minima with resizable splitters and vertically scrolling control panels.
- Added one linked orientation, zoom, rotation, and fit toolbar for the 2D slice and 3D CAD views.
- Made the shared endpoint and anatomy selectors authoritative across both 2D and CAD displays.
- Changed CAD scalar-bar labels to a high-contrast near-white colour on the dark viewport.

### Performance

- Coalesced continuous CAD interactions to 30 frames per second and renders a reduced-resolution preview while dragging.
- Restores a full-quality frame after interaction and debounces expensive mesh and opacity refreshes.
- Reuses loaded immutable biological volumes and disables costly volume shading in the cross-platform off-screen renderer.

### Scientific scope

- No Layer 3.1 dose, BED, EQD2, MLQ, EUD, TCP, mask, or geometry calculation was changed.
- The physical workflow remains validated through Layer 2.2. Layer 3.1 remains computationally verified research software and is not clinically validated.

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
