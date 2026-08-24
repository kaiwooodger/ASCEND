# ASCEND Layer 3.1: radiobiological response modelling

Layer 3.1 contains three independent research formalisms. They are parallel consumers of validated physical dose and treatment context. Layer 2.1 summary metrics are not inputs to any biological equation.

- **3.1A Conventional LQ reference:** voxelwise P/Q BED and EQD2.
- **3.1B High-dose SFRT response:** configured survival and survival-equivalent tumour EUD over the complete GTV voxel-dose distribution.
- **3.1C Modelled therapeutic ratio:** theoretical normal-tissue survival comparison at equal modelled tumour survival.

## Fundamental P/Q model

For physical dose delivered in fraction `f`:

```text
P(x) = sum_f d_f(x)
Q(x) = sum_f d_f(x)^2
```

When component `k` consists of `n_k` identical fractions and its validated component-total dose is `D_k(x)`, ASCEND uses the exact shortcut:

```text
P_k(x) = D_k(x)
Q_k(x) = D_k(x)^2 / n_k
```

For alpha/beta `A > 0`:

```text
BED(x; A)  = P(x) + Q(x) / A
EQD2(x; A) = BED(x; A) / (1 + 2 / A)
```

`P` has units Gy and `Q` has units Gy squared. BED is labelled Gy BED and must not be treated as physical absorbed dose.

## Dose and fraction history

Layer 3.1 supports validated per-fraction dose inputs and validated component-total dose inputs under an explicit identical-fraction assumption. Different treatment components retain independent fraction counts. ASCEND does not flatten a composite course into one total dose and one total fraction count.

Every contributing dose must already share the same validated physical geometry. Matching array dimensions alone are insufficient. Layer 3.1 performs no implicit rigid registration, deformable registration, resampling, dose warping, or contour reconstruction.

## ROI history

Every rasterised ROI state is represented by an `ROIInstance` containing ROI identity, validated reference geometry, treatment component, timepoint, volume, and a hash-verified Layer 1 mask reference. This prevents one static GTV or OAR from being assumed to represent an entire composite course.

ROI-specific results use independent identity-bound alpha/beta assignments. Overlapping ROIs may be analysed independently. A combined spatial tissue-parameter field is blocked when overlapping ROIs carry conflicting parameters; no implicit precedence is used.

## Outputs

For BED and EQD2, ASCEND records mean, D2, D5, D50, D90, D95, D98, minimum, and maximum, plus cumulative BED-volume and EQD2-volume histograms. ROI summaries operate on ROI vectors without allocating full maps. Full BED/EQD2 NPZ maps are generated only through explicit export.

## Legacy 3.1A compatibility mappings of the Layer 2.1 metric suite

Layer 3.1A retains biological counterparts and contextual mappings of the six Layer 2.1 metrics for backward-compatible presentation. These records are not inputs to 3.1B EUD or 3.1C modelled therapeutic ratio and are not a claim that every physical metric has a literal biological counterpart. They are not all direct BED/EQD2 transformations:

- peripheral and high-dose coverage are **biological coverage analogues**;
- high-dose volume fraction is **geometry carried forward**;
- mean peak and valley dose are **biological transformations** evaluated from voxelwise maps;
- peak–valley contrast is a **derived biological contrast**.

Coverage is not "BED of V95." It is the percentage of the relevant validated mask meeting 95% of the biological prescription. For component prescription `R_k` delivered in `n_k` identical fractions:

```text
P_Rx = sum_k R_k
Q_Rx = sum_k R_k^2 / n_k
BED_Rx = P_Rx + Q_Rx / (alpha/beta)
EQD2_Rx = BED_Rx / (1 + 2/(alpha/beta))
coverage_BED = V[BED(x) >= 0.95 BED_Rx]
coverage_EQD2 = V[EQD2(x) >= 0.95 EQD2_Rx]
```

Composite-course coverage is not calculated when component-specific prescription history is incomplete. This prevents a complete-course Rx from being applied to one component or an LRT-only Rx from being treated as a composite prescription.

Mean peak and valley metrics are evaluated directly from P/Q inside VTV_H and VTV_L using their identity-bound alpha/beta assignments. This is `mean[BED(D(x))]`, not the generally unequal `BED(Dmean)`. The ratio is labelled **BED peak–valley contrast** or **EQD2 peak–valley contrast** and marked as derived. Different alpha/beta assignments are permitted but explicitly warned because the resulting contrast is parameter-dependent.

High-dose volume fraction is **geometry-only; unchanged by biological dose transformation**. It is carried forward as context and does not generate a BED or EQD2 value.

When VTV_H and VTV_L share one alpha/beta, EQD2 is a constant scaling of BED. BED and EQD2 peak–valley contrasts are then mathematically identical and are flagged as redundant rather than presented as independent findings.

Whole-GTV BED mean, BED D95, EQD2 mean, and EQD2 D95 are stored as additional contextual endpoints. They remain separate from the six Layer 2.1 mappings and support LRT versus LRT+cERT comparison.

For LRT+cERT courses, conventional-background dose contributes to P/Q in both peak and valley regions. Valley and ratio records explicitly warn that cERT background is included.

## Cache and sensitivity analysis

The reusable P/Q basis cache is keyed by validated dose hashes and UIDs, component identities, dose-history method, fraction counts, common geometry, algorithm version, and basis-relevant configuration. Alpha/beta is excluded. Sensitivity sweeps reuse one verified P/Q basis.

## Interpretation limits

The warning `conventional_lq_high_dose_caution` is retained. BED/EQD2 at very high dose per fraction carries model uncertainty. Layer 3.1 is not TCP, NTCP, a bystander, vascular, immune, or clinical outcome model. It is not a complete representation of Lattice Radiotherapy biology.

Passing analytic LRT-only, LRT+cERT, and same-dose/different-fractionation scenarios is computational verification. It is not clinical validation on actual LRT+cERT DICOM datasets.

The future Layer 3.2 handoff can consume P, Q, validated ROI history, treatment components, physical geometry, and explicit parameters without reopening DICOM.

## Layer 3.1B: high-dose SFRT survival and EUD

For configured tumour parameters `alpha`, `beta`, `delta`, repair half-time and treatment delivery time `T`:

```text
lambda = ln(2) / repair_half_time
z_i = lambda*T + delta*D_i
G(z) = 2*(z + exp(-z) - 1)/z^2
SF_i = exp[-alpha*D_i - beta*G(z_i)*D_i^2]
SF_bar_T = sum_i(v_i*SF_i), sum_i(v_i)=1
SF_MLQ(EUD_T) = SF_bar_T
```

`G(z)` uses a series expansion near zero. EUD is solved with a bounded one-dimensional root solver and stores the residual and tolerance. Direct equal-volume native dose voxels are used, so no DVH bin-width approximation enters the result.

All parameters and their sources are mandatory. ASCEND supplies no universal biological defaults and never infers parameters from an ROI name. The shared fraction-event engine accepts explicit validated fraction-dose objects or a declared repeated-identical component total. Same-fraction integrated components are physically summed before MLQ evaluation; sequential events remain separate. Unresolved fraction history, geometry, or delivery-time provenance is blocked rather than guessed.

The implemented `G(z)` dose-protraction function is sourced to Liu F et al., *Radiotherapy and Oncology* 2017;122(2):286–294, DOI `10.1016/j.radonc.2016.11.006`, PMID `27871671`. The complete configured survival/EUD use remains provisional research modelling.

## Layer 3.1C: modelled therapeutic ratio

Using the same heterogeneous physical dose vector with a separately configured normal-tissue parameter set:

```text
SF_bar_N_LRT = sum_i(v_i*SF_N(D_i))
SF_N_EUD = SF_N(EUD_T)
TR = SF_bar_N_LRT / SF_N_EUD
```

This output is labelled **Modelled therapeutic ratio**. It is not clinical therapeutic ratio, clinical benefit, NTCP, toxicity prediction, OAR compliance, or a PASS/FAIL decision. Missing normal-tissue parameters suppress 3.1C only; 3.1A and 3.1B remain independent.

## Treatment context and applicability

Every result stores the treatment approach, component identities, component prescription and fractionation, optional dates and gaps, selected analysis component, dose and plan UIDs, geometry identity, provenance, and treatment-context hash. No registration, dose warping, gap correction, repopulation, or arbitrary course-level MLQ accumulation is implicit.

