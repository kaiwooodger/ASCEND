# ASCEND 0.8.0

Validated-mask biological gating baseline. Validated through Layer 3.1.

## Biological-mask safety correction

- Changing configured OAR rasterisation now invalidates Layer 3.1 as well as Layer 1 and the physical layers.
- Layer 2.1, Layer 2.2, Layer 3.1, and Layer 3.1 sensitivity sweeps reject stale Layer 1 results.
- Layer 3.1 continues to require every identity-bound tissue parameter to resolve to an ROI whose current Layer 1 inventory state is `rasterised`.
- A `not_rasterised` OAR produces no BED endpoint, EQD2 endpoint, histogram, or volume summary even if an obsolete mask key remains in an older artifact.
- The Qt workstation hides stale biological values and removes stale Layer 1 ROIs from the Layer 3.1 selector until Layer 1 is rerun.
- A retained stale record is never presented as a current result; the workstation replaces it with an explicit blocked state if calculation is requested before Layer 1 is rerun.

## Verification

- Complete automated suite: 145 tests passed.
- Added regression coverage for OAR-removal invalidation, direct `not_rasterised` assignment rejection, stale Layer 1 execution blocking, stale sensitivity-sweep blocking, and Qt suppression of retained stale values.
- Layer 1 locked source SHA-256: `dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490`.
- Layer 2.1 locked source SHA-256: `4ddfa7eef71118db8edb40eba7331c3ee70a07021cd5386caf6f5f7c00cb3621`.
- Layer 2.2 locked source SHA-256: `2a45da69f21428078ec227fb69e0175168f0528d39432bdc60a3724b313eeb24`.

## Scientific scope

The Layer 1, Layer 2.1, Layer 2.2, and Layer 3.1 scientific formulas are unchanged. This release corrects dependency invalidation, execution gating, and GUI state presentation.
