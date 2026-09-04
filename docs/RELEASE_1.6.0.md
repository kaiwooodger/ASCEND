# ASCEND 1.6.0

Release identifier: `ASCEND-1.6.0-UNIFIED-INDIVIDUAL-VERTEX-QA-20260902`

Release date: 2026-09-02

## Release objective

ASCEND 1.6.0 consolidates individual-vertex presentation into one Physical workflow page below Layer 2.2. It removes the need to move between Layer 2.1 and Layer 2.2 pages to relate vertex dose, geometry, profile, graph, saddle, and OAR evidence.

## Unified Individual vertex QA page

The page contains six sub-tabs:

1. `Hover graph overview` — interactive Layer 2.2 graph with node/edge hover evidence, projection, zoom, pan, rotation, selection, summary tables, and provenance.
2. `Vertex profiles` — stored radial profiles, background correction, shell mean, dosimetric/geometric diameter, penumbra, gradient, and background D50.
3. `Per-vertex QA` — stored V95 RxH, Dmean, D95, Dmax, volume, local FWHM, nearest-neighbour distance, and vertex-analysis metadata.
4. `Vertex layout / FWHM` — stored centroid layout, nearest-neighbour distance labels, FWHM colour encoding, volume marker sizing, vertex hover cards, and global FWHM summaries.
5. `Saddle graphs` — stored midpoint/saddle evidence, edge modes, markers, paths, diagnostic corridors, and exclusions.
6. `OAR geometry` — descriptive aggregate and per-vertex OAR separation, overlap, nearest-vertex identity, and audit findings.

The top selector bar links vertex and edge identities across compatible views. Selection can originate from selectors, the graph, the vertex layout, profile controls, QA tables, saddle rows, or OAR rows.

## Existing page boundaries

Layer 2.1 retains the six locked metric cards, supporting-output selection, supporting context, export, and provenance. Layer 2.2 retains execution controls, status/warnings, and the hash-verified 3D masks/dose viewer. The consolidated page is a presentation adapter over their stored results.

## Scientific and validation boundary

- No dose, DVH, FWHM, graph, profile, saddle, or OAR geometry calculation occurs in the GUI.
- The Layer 2.1 and Layer 2.2 scientific services and stored schemas are unchanged.
- Existing 1.5.0 cases remain readable. Missing optional evidence is displayed as unavailable rather than inferred.
- Physical validation scope remains unchanged through Layer 2.2.
