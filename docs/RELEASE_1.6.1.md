# ASCEND 1.6.1

Release identifier: `ASCEND-1.6.1-ICRU91-PADDICK-GRADIENT-20260907`

Release date: 2026-09-07

## Release objective

ASCEND 1.6.1 replaces the custom radial-shell vertex dose-profile extension introduced before 1.6.0 with a dose-falloff presentation familiar from stereotactic radiosurgery reporting. The authoritative gradient quantity is the whole-plan Paddick gradient index:

\[
GI = \frac{PIV_{half}}{PIV}
   = \frac{V(D \ge 0.5\,Rx_H)}{V(D \ge Rx_H)}.
\]

`PIV_half` is the physical volume receiving at least half the configured high-dose prescription. `PIV` is the physical volume receiving at least the configured high-dose prescription.

## Removed outputs

The Layer 2.2 extension no longer calculates, stores, displays, or exports:

- radial shell dose summaries or IQR bands;
- background-corrected vertex profiles;
- r80, r50, or r20 crossings;
- radial dose diameter or 80–20 penumbra;
- radial Gy/mm gradient or profile anisotropy.

Existing 1.6.0 result files are not rewritten. Their legacy `vertex_profiles` record is ignored by the 1.6.1 interface. Rerunning Layer 2.2 creates the 1.6.1 dose-gradient result.

## New stored result

`layer2_2_extensions.dose_gradient` records:

- configured `Rx_H` and prescription source;
- `PIV_half` at 50% of `Rx_H`;
- `PIV` at 100% of `Rx_H`;
- Paddick GI as `PIV_half / PIV`;
- an isodose-volume profile from 25% through 125% of `Rx_H` in 5% increments;
- target-count and high-dose-target-volume context;
- native-grid volume basis, provenance, status, and warnings.

The calculation includes every finite native RTDOSE voxel meeting the threshold. It uses physical voxel volumes and performs no interpolation, radial sampling, smoothing, patient-contour inference, or per-vertex apportionment.

## Interpretation boundary

Lower GI denotes steeper fall-off, but valid plan comparison requires matched target volume and similar conformity. ASCEND does not apply a universal pass/fail threshold. It warns when:

- the 50% or 100% prescription isodose touches the RTDOSE grid boundary;
- more than one target contributes combined low-dose wash;
- any resolved high-dose target volume is below 1 cc.

Missing or invalid `Rx_H` and an empty prescription isodose volume return explicit non-calculated states. Prescription changes invalidate Layer 2.2.

## References

- International Commission on Radiation Units and Measurements. [ICRU Report 91: Prescribing, Recording, and Reporting of Stereotactic Treatments with Small Photon Beams](https://www.icru.org/report/icru-report-91-prescribing-recording-and-reporting-of-stereotactic-treatments-with-small-photon-beams-2/). 2017.
- Paddick I, Lippitz B. [A simple dose gradient measurement tool to complement the conformity index](https://pubmed.ncbi.nlm.nih.gov/18503356/). *Journal of Neurosurgery*. 2006;105(Suppl):194–201. doi:10.3171/sup.2006.105.7.194.

## Unchanged validated boundaries

The six locked Layer 2.1 metrics and the locked Layer 2.2 nearest-neighbour graph, 3 mm midpoint-sphere sampling, iPVDR endpoint, and saddle calculation are unchanged. ASCEND remains research and technical-validation software, not approved clinical decision software.
