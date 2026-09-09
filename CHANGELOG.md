# Changelog

All notable ASCEND changes are recorded here. Releases follow immutable Git tags; retrospective analyses must record the exact tag and commit.

## [1.8.0] — 2026-09-09

### Changed

- Made imported treatment-planning-system DVHs the sole authority for RTSTRUCT ROI eligibility.
- Rasterise every and only the ROIs matched to imported DVHs; unmatched contours are excluded from Layer 1 and every downstream selector.
- Restrict Layer 3.1 alpha/beta assignments and spatial-field analysis to DVH-verified, rasterised ROI identities.
- Prefill the rasterisation scope, downstream ROI menus, and supported protocol-native endpoints from the imported DVH.

### Validation

- Require every imported structure DVH to contain valid D2% and D95% dose endpoints; otherwise fail with `TPS_DVH_REQUIRED_ENDPOINTS`.
- Bind DVHs to immutable RTSTRUCT SOP Instance UID and ROI number identities, using a unique normalised structure name only when the source lacks explicit identity fields.
- Detect DVH source, patient, RTSTRUCT, ROI, and persisted-verification changes before Layer 1 calculation.
- Record DVH verification evidence on each ROI inventory entry and exclude unverified entries from Layers 2 and 3.

### Scientific scope

- ROI eligibility and workflow safety contracts changed. Locked dose, geometry, physical-metric, and radiobiological formulae are unchanged.

## [1.6.8] — 2026-09-09

### Added

- Added a coherent A4 PDF report assembled from current stored ASCEND results without recalculation.
- Added grouped export-screen checkboxes for report overview, RTPLAN delivery, individual Layer 2.1 primary metrics, supporting physical evidence, biological/modelled results, warnings, and provenance.
- Added select-all and clear-all controls and persisted the selected report sections in the case configuration.

### Corrected

- Shortened Layer 1 publication, archive, cache, and cache-materialisation staging names so ASCEND-managed temporary paths remain below the legacy Windows path limit for ordinary case roots.
- Retained same-filesystem atomic publication and abandoned-staging cleanup while removing long run identifiers, cache hashes, duplicated artifact names, and full UUIDs from temporary path components.

### Reporting contract

- Keep calculation status, applicability, units, warnings, limitations, interpretation boundaries, and provenance adjacent to the selected results.
- Refuse unknown report sections, an empty selection, and stale stored results.
- Preserve the 1.6.6 RTPLAN control-point beam-on calculation and all locked scientific metric definitions.

## [1.6.6] — 2026-09-09

### Corrected

- Replaced the single-rate RTPLAN beam-on estimate with interval-by-interval integration over the beam `ControlPointSequence`.
- Converted each consecutive cumulative-meterset-weight increment to MU using its referenced `BeamMeterset`, then divided by the `DoseRateSet` active at the interval's starting control point.
- Preserve DICOM control-point inheritance while refusing to borrow a dose rate from the interval end point.
- Fail closed when meterset weights, control-point order, dose rate, final weight, referenced meterset, or primary dosimeter units cannot support a valid calculation.

### Evidence and presentation

- Record the effective dose-rate source control point, cumulative MU, interval MU, interval dose rate, and interval beam-on seconds in RTPLAN delivery metadata version 2.
- Report control-point beam-on time separately from `BeamDeliveryDurationLimit` and retain pre-1.6.6 estimated-time field names as compatibility aliases.
- Label the workstation result as control-point beam-on time and state that it excludes setup, imaging, mechanical-transition, and inter-beam overhead.

### Scientific scope

- Beam-on time is derived from planned RTPLAN metadata. It is not a treatment-record measurement of delivered time.
- Physical dose, Layer 2 metrics, Layer 3 biological calculations, and the 1.6.5 identity-bound anatomical-mask contract are unchanged.

## [1.6.5] — 2026-09-08

### Corrected

- Split the former overloaded OAR configuration into `layer1_rasterisation_rois`, `layer21_oar_geometry_rois`, and `layer31c_oar_rois`.
- Removed all Layer 3.1C name and canonical-name fallback matching. Layer 3.1C now resolves only RTSTRUCT SOP Instance UID plus ROI number against the current rasterised Layer 1 inventory.
- Removed the GTV normal-tissue fallback. Layer 3.1C is `NOT_ASSESSED` when no analytical OAR is selected.
- Made a partially unresolved multi-OAR selection block the whole Layer 3.1C branch with `ROI_REQUIRES_LAYER1_RASTERISATION`.
- Restricted Layer 3.1C display names, canonical mask keys, mask hashes, and structure provenance to values derived from the matched Layer 1 inventory.

### Workflow

- Added separate workstation editors for Layer 1 rasterisation requests, Layer 2.1 OAR geometry masks, and Layer 3.1C analytical OAR masks.
- Populated downstream OAR selectors only from current Layer 1 inventory records whose `rasterisation_status` is `rasterised`.
- Limited Layer 1 invalidation to Layer 1 rasterisation-set changes. Layer 2.1 geometry-only changes stale Layer 2.1; Layer 3.1C selection changes stale Layer 3.1 and Layer 3.2 while leaving Layer 1 current.

### Compatibility

- Preserved `oar_structures` as migration input for pre-1.6.5 case files. It is not consumed by scientific services.
- Legacy identity-bound OAR records migrate to the separated fields. Legacy name-only records are not promoted into Layer 3.1C analytical scope.

### Scientific scope

- Layer 1 remains the sole owner of RTSTRUCT contour interpretation and native-dose-grid anatomical mask creation.
- Layer 3.1 never creates, reconstructs, propagates, or name-resolves anatomical masks.
- Physical and radiobiological formulae are unchanged.

## [1.6.1] — 2026-09-07

### Changed

- Removed the custom Layer 2.2 per-vertex radial-shell dose profiles, background correction, r80/r50/r20 crossings, radial penumbra, and maximum radial-gradient outputs.
- Added whole-plan Paddick gradient-index reporting in the ICRU Report 91 stereotactic-treatment context: `GI = PIV_half / PIV` at 50% and 100% of configured Rx_H.
- Replaced the radial-profile interface and exports with PIV half, PIV, GI, and a native-grid 25%–125% Rx_H isodose-volume profile.
- Made Rx_H and treatment-component changes invalidate Layer 2.2 because the new gradient result depends on prescription context.

### Safety and interpretation

- GI is not assigned to individual vertices; this avoids non-standard allocation of overlapping low-dose wash in multi-target plans.
- Missing Rx_H, empty PIV, dose-grid boundary contact, multiple targets, and sub-cc target context produce explicit unavailable states or warnings.
- GI remains a plan-comparison descriptor, not a clinical pass/fail threshold. Lower GI is interpreted only for matched target volume and similar conformity.

### Scientific scope

- The hash-locked six-metric Layer 2.1 implementation and locked Layer 2.2 graph, midpoint-sphere, iPVDR, and saddle calculations are unchanged.
- The ICRU 91-context gradient extension uses native RTDOSE voxels and physical voxel volumes without interpolation, smoothing, or inferred patient contour.

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
