# Changelog

All notable ASCEND changes are recorded here. Releases follow immutable Git tags; retrospective analyses must record the exact tag and commit.

## [1.6.0] — 2026-09-02

### Added

- Added `Individual vertex QA` as a dedicated Physical workflow page directly below Layer 2.2.
- Consolidated the hover graph overview, vertex profiles, per-vertex dose QA, vertex layout and global FWHM, saddle graphs, and OAR geometry into six sub-tabs.
- Added linked vertex and edge selectors. Graph clicks, profile selection, QA-table rows, vertex-layout clicks, saddle rows, and OAR rows synchronise the same stored identity across the workspace.
- Added hover evidence for Layer 2.2 graph nodes and edges and visible selection highlighting for graph and Layer 2.1 vertex-layout markers.
- Added a dedicated interactive workspace guide.

### Changed

- Layer 2.1 now retains primary metrics, supporting context, and provenance on its original page; vertex-specific presentation is grouped in the unified workspace.
- Layer 2.2 retains calculation controls and the hash-verified 3D masks/dose viewer on its original page; graph/profile/saddle presentation is grouped in the unified workspace.

### Scientific scope

- The unified page consumes stored Layer 2.1 and Layer 2.2 records and performs no scientific recalculation.
- The hash-locked six-metric Layer 2.1 implementation and validated Layer 2.2 graph implementation are unchanged.

## [1.5.0] — 2026-08-31

### Added

- Added a dedicated third Layer 2.1 section, `Vertices layout`, matching the Layer 2.2 graph interaction pattern with projection, zoom, pan, rotation, fit, labels, and stored nearest-neighbour connections.
- Added hover QA menus for vertex D95, V95 relative to RxH, mean/maximum dose, volume, nearest-vertex distance, local FWHM, native-axis FWHM widths, and warnings.
- Added per-vertex local FWHM evidence from linearly interpolated half-local-maximum dose profiles through each vertex dose maximum.
- Added a Global FWHM tab reporting the average, median, minimum, maximum, and individual native-axis values.

### Changed

- Encoded individual vertices with a low-to-high FWHM colour gradient; marker size now independently represents vertex volume.
- Removed empty-result expansion from Layer 1 evidence tabs, Layer 2.2 graph controls, Layer 3.1 fraction history, and Layer 3.2 parameter/result tables.

### Scientific scope

- FWHM and vertex-layout values are optional supporting QA evidence and are not clinical endpoints.
- The hash-locked six-metric Layer 2.1 implementation and validated Layer 2.2 graph calculation are unchanged.

## [1.4.1] — 2026-08-27

### Changed

- Embedded the Layer 3.1 spatial viewer directly in the ASCEND workstation and removed the dedicated top-level viewer window.
- Replaced separate slice and CAD tabs with one dense 2×2 treatment-planning workspace containing transverse, sagittal, coronal, and 3D biological/CAD panes.
- Added top endpoint tabs and bottom workspace tabs while retaining one authoritative field, colour-range, anatomy, ROI, and crosshair state.
- Linked mouse zoom, pan, and 3D rotation changes across the four panes in addition to the shared orientation, zoom, rotation, and fit toolbar.
- Moved detailed 3D mode, geometry, opacity, build-status, and export controls into an optional non-modal CAD controls dialog.
- Expanded the embedded Map workspace with a 600-pixel viewer minimum and a focused tab layout that collapses duplicated page chrome, increasing four-pane height without reopening a detached window.
- Restored the visible vertex/valley/other-GTV residual-survival contribution figure to Step 15 and linked its segments back to the corresponding unified-viewer masks.
- Made Layer 3.1 CAD open as orthogonal biological-effect slices while retaining volume, surface, isosurface, and combined modes in the optional CAD controls.
- Removed Layer 3.2 comparison-page padding and the white graph canvas, reduced per-panel chrome, and expanded synchronized side-by-side maps into the available workspace.

### Scientific scope

- No physical-dose, BED, EQD2, MLQ, EUD, TCP, mask, geometry, or validation calculation changed.
- A revision-to-revision kernel audit reproduced the frozen Layer 3.1B synthetic SF and EUD exactly in ASCEND 1.3.5, 1.4.0, and 1.4.1.

## [1.4.0] — 2026-08-27

### Corrected

- Maximised the main workstation and unified Layer 3.1 viewer against the current monitor's available geometry, with responsive splitter allocation that prioritises the slice and CAD canvases.
- Opened the unified Layer 3.1 spatial viewer as a dedicated top-level window and repaired cross-runtime field materialisation identity checks.
- Added explicit default normal-cell kinetic preset selection for N1–N3 workstation scenarios.
- Evaluated Layer 3.1C normal-tissue survival over validated configured OAR masks and added per-OAR normal-tissue survival-equivalent EUD summaries.

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
