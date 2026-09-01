# ASCEND 1.5.0 Layer 1–3 GUI whitespace audit

Audit date: 2026-09-01

Audit viewport: 1800 × 1100 pixels using populated synthetic Layer 1/2 evidence and explicit empty/not-run Layer 3 states.

## Layer 1

Finding: the active evidence table expanded to the full remaining viewport when it contained only an empty-state row. This produced a large white panel with no additional information.

Correction: Layer 1 findings, RTPLAN delivery, and Eclipse audit tables now fit their visible rows within bounded scroll areas. The active evidence tab resizes to its current content. Remaining viewport space stays outside the evidence card.

## Layer 2

Finding: the Layer 2.2 graph-controls card shared vertical stretch with the graph workspace, separating its title, description, and controls with several hundred pixels of empty space.

Correction: graph and vertices control cards use maximum-content vertical sizing. The graph/canvas workspace receives the recovered space. Layer 2.1 vertex colour now encodes local FWHM on a labelled blue-to-purple gradient; marker size independently encodes volume.

## Layer 3

Finding: an empty Layer 3.1 fraction-history table and disabled Layer 3.2 parameter/result tables reserved populated-result heights.

Correction: these tables dynamically fit visible rows with bounded maximum heights. Layer 3.2 context tabs are bounded so empty states do not displace downstream evidence. Four-pane spatial viewer space remains unchanged because it is functional display area, not decorative whitespace.

## Scope

The audit changed presentation sizing and visual encoding only. It did not change dose, geometry, Layer 2.1 metrics, Layer 2.2 graph calculations, BED, EQD2, MLQ, EUD, TCP, or non-local biological calculations.
