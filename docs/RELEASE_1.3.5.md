# ASCEND 1.3.5

Release identifier: `ASCEND-1.3.5-SPATIAL-MLQ-POISSON-TCP-20260824`

ASCEND 1.3.5 introduces Layer 3.1D, a provenance-preserving spatial
MLQ-Poisson tumour-control probability research layer.

Layer 3.1D consumes the authoritative Layer 3.1B tumour survival state. It
does not recalculate dose, fraction history, MLQ survival, EUD, masks,
geometry, BED, or EQD2.

Primary additions:

- qualified radiation-only and repopulation-corrected Poisson TCP;
- initial and expected residual clonogen burden;
- vertex, valley, and remaining-tumour residual-burden decomposition;
- delayed exponential repopulation with explicit timing provenance;
- optional clonogen-density sensitivity analysis;
- strict Layer 3.1D gates, dependency invalidation, export, and provenance;
- residual-clonogen overlays in the existing unified 2D/CAD viewer.

Validation position:

- Layers 1–2.2 retain their existing validation position.
- Layers 3.1A–3.1D are computationally verified research models.
- Layer 3.1D is biologically unvalidated and is not a clinical outcome,
  toxicity, TCP calibration, or treatment-decision system.
