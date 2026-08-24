# Validation status and remaining evidence

## Completed evidence

- Frozen Layer 1, Layer 2.1, and Layer 2.2 sources remain byte-identical.
- Existing PHPROLRT01 Layer 1 dose and masks remain bitwise identical to the validated baseline.
- Existing Layer 2.1 and Layer 2.2 numerical regression evidence remains unchanged.
- The native PySide6 GUI opens, renders all ten workflow pages, and exposes no scientific calculation code.
- The dedicated Eclipse synthetic 5V5 Layer-2.2B DICOM fixture completes through Layer 2.2 with explicit vertex provenance.
- The original PHPROLRT01 graph retains its three-component warning and is rendered for visual inspection.

## PHPROLRT01 graph inspection

The stored graph contains three visibly separated nearest-neighbour subgraphs in the centroid projection. The five stored edges and their component colours are internally consistent with the frozen undirected 1-nearest-neighbour rule; the warning is therefore not a serialization or GUI artefact.

This does not prove that the three subgraphs represent the intended treatment lattice. The nodes were derived from 26-connected components of one aggregate VTV_H mask rather than explicit RTSTRUCT vertices. The `connected_components_derived` provenance and `graph_disconnected` warning must remain. The CC07–CC08 edge also has an iPVDR of approximately 9.46 because its local valley D50 is approximately 1.44 Gy, while the other four edge iPVDR values are approximately 1.34–1.44. The edge table exposes this result for case-level review without automatically excluding it.

## Eclipse DVH evidence located

The local evidence set contains:

- `<local-validation-evidence>/Layer1_Eclipse_DVH_Comparison.csv`
- `<local-validation-evidence>/Layer1_Eclipse_DVH_Comparison.json`
- `<local-validation-evidence>/Layer1_Eclipse_10Test_Debug.json`

For PHPROLRT01, the recorded comparison covers volume, D95, minimum, maximum, and mean dose for nine structures. The dose comparisons largely pass the stated provisional criteria. Recorded exceptions and warnings remain visible, including VTVH anatomical-volume disagreement and the Lung_subHD maximum-dose discrepancy.

This does not constitute the requested complete formal endpoint set. D90, D50, V95, and V100 are not present in the stored comparison table, and the case RTPLAN is unapproved. ASCEND must not label the Eclipse validation stage complete until those predetermined endpoints and acceptance criteria are supplied and evaluated on characterised cases.

## Required external evidence

- A characterised LRT plan with independently confirmed Rx_L and Rx_H.
- Explicit vertex structures and, where applicable, an explicit planned valley structure.
- Eclipse exports for D95, D90, D50, Dmean, V95, and V100 with frozen agreement criteria.
- Test cases for simultaneous integrated, sequential-boost, and composite-dose contexts.
- At least one approved or formally designated research-reference plan if approval status is relevant to the validation claim.

Layer 3 remains blocked by scope. No BED/EQD2, TCP, NTCP, reaction-diffusion, or bystander implementation has been added.
