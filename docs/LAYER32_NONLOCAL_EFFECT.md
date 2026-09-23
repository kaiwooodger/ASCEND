# Layer 3.2 non-local biological reinterpretation

> **CURRENT STATE: RESEARCH IMPLEMENTATION.** Layer 3.2 requires current Layer 1, Layer 2.2, and Layer 3.1 evidence. It remains separate from established-radiobiology Layer 3.1.

**RESEARCH MODEL · NOT CLINICALLY CALIBRATED · NOT A TOXICITY PREDICTION**

Layer 3.2 is a downstream reinterpretation:

```text
validated physical dose and masks
        + frozen Layer 2.2 graph
        + Layer 3.1 P/Q fraction history
        + explicit model parameters
        -> provisional non-local biological-effect analysis
```

The baseline is `S_LQ(x) = exp[-alpha P(x) - beta Q(x)]`. The no-uptake reaction-diffusion model is:

```text
dC_k/dt = D_k Laplacian(C_k) - lambda_k C_k + E_k(x)
H(x) = integral [w_ROS C_ROS(x,t) + w_cytokine C_cytokine(x,t)] dt
S(x) = S_LQ(x) exp[-s H(x)]
```

The final survival field is inverted through the configured LQ coefficients to create a model-derived biological effect-equivalent field. This field is not absorbed dose. The additional field is final effect-equivalent dose minus baseline LQ effect-equivalent dose.

## Alpha/beta inputs and sensitivity

Layer 3.2 has two explicit coefficient modes:

- **Match current Layer 3.1 tumour inputs** reads alpha and beta from the applicable stored Layer 3.1B tumour parameter set and records its parameter identity and scenario.
- **Manual Layer 3.2 alpha and beta** uses independent finite positive coefficients entered in the Layer 3.2 controls.

The resolved source, alpha, beta, and alpha/beta ratio are stored with every Layer 3.2 result. Changing either the source mode or the manual values invalidates prior Layer 3.2 evidence.

Layer 3.2 alpha/beta sensitivity can be disabled, inherit the complete enabled Layer 3.1 tumour-site sensitivity contract, or use an independent Layer 3.2 contract. Both enabled modes require a tumour-site label, source or rationale, finite positive range, 2–101 samples, and an explicit hold-alpha or hold-beta rule. Each sample stores the derived alpha, beta, SF2, mean GTV baseline and final survival, baseline and biological effect-equivalent iPVDR medians, and signed shifts. The non-local mediator field and physical dose remain fixed across the sweep.

`H` is displayed as **Cumulative non-local mediator exposure**: time-integrated weighted exposure to the modelled ROS-like and cytokine-like fields. Higher values indicate stronger accumulated modelled signalling. It is dimensionless and is not physical dose, measured concentration, toxicity probability, or clinical risk. “Hazard field” is retained only as an advanced technical synonym and internal compatibility name.

The default displayed consequence is `B_NL(x) = 100[1-exp(-sH(x))]%`, labelled **Additional modelled survival reduction relative to LQ**. It is not toxicity or a cell-killing probability.

The model domain is cropped to the GTV plus a configured physical margin and may be resampled to the configured model-grid spacing. Both transformations are recorded. Outside the model crop, OAR cumulative mediator exposure is treated as zero; physical and baseline LQ context remain available.

Layer 3.2 uses the unchanged Layer 2.2 graph and 3 mm midpoint sampling spheres. Edge-line profiles are stored only for display. Biological iPVDR shift is signed because non-local effects can compress rather than increase peak–valley contrast.

All configured OARs must already be identity-bound and rasterised in the current Layer 1 result. OAR outputs are descriptive biological spill metrics, not protocol compliance or clinical pass/fail determinations.

## Spatial viewer and export

The Qt workstation opens with synchronized baseline-LQ survival, final-survival,
and additional-modelled-reduction panels. Slice, zoom, crosshair, anatomy
visibility, and complete-volume colour scaling are shared. Absolute scaling is
the default; case-relative scaling is explicitly exploratory. The selected
voxel panel shows physical dose, baseline survival, H, s, sH, the non-local
multiplier, relative reduction, and final survival.

The three-dimensional viewer uses absolute consequence surfaces at 2.5%, 5%,
10%, and 20% additional modelled survival reduction rather than percentiles of
the current case. At `s = 0.0029365813` these correspond to H values of 8.6,
17.5, 35.9, and 76.0. Only the 5% and 10% surfaces are visible by default.
Surfaces can be overlaid with stored GTV, aggregate vertex, and configured OAR
surfaces in DICOM patient LPS coordinates.

The wireframe crop boundary is always available because Layer 3.2 models the
GTV plus the configured physical margin rather than the whole patient.  No
non-local effect outside this stored crop is implied by the viewer.

Spatial export provides VTI for the complete stored scalar volume, VTP/PLY/GLB
for coloured absolute consequence surfaces, 3MF for colour-capable fabrication,
and one STL per effect threshold plus stored anatomical-mask STLs. STL is explicitly
geometry-only and cannot retain scalar values, colours, or opacity.  Every
export includes a manifest describing coordinate system, thresholds, crop
geometry, and limitations.

The result records three comparison states. Physical/LQ baseline and the
no-vascular-sink non-local model are calculated. Anatomical vascular sink is
reported as unavailable because Layer 3.2 accepts neither a validated vessel
mask nor an uptake model. It is never simulated implicitly.

The **Modelled regional exposure and consequence** table reports GTV, each
vertex, the frozen Layer 2.2 valley-region union, peri-GTV 0–5 mm and 5–10 mm
shells, and each configured OAR. Columns include mean and P95 H, mean and
maximum additional reduction, volume at or above 5%, and mean final-survival
change.
