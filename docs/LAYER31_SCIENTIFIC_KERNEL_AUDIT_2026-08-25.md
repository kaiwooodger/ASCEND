# ASCEND Layer 3.1 scientific-kernel audit

Date: 2026-08-25  
Scope: conventional LQ BED/EQD2, fraction-resolved Guerrero–Li MLQ, survival-equivalent EUD, modelled therapeutic ratio, direct-clonogenic Poisson TCP, and their 2D/3D presentation handoff.

## Audit conclusion

The inspected kernels are internally consistent with their declared equations and software contracts. The implementation remains a research model and is not clinically validated. No code path is permitted to replace missing parameters, substitute tumour parameters for OAR parameters, or force TR/TCP to a non-zero value.

The audit identified and corrected five implementation defects:

1. The 3D workspace defaulted to a scalar surface, so the result appeared as ribbons instead of a voxel volume.
2. The raw MLQ-survival opacity transfer made the lowest valid survival values transparent. A render-only sentinel now separates out-of-mask voxels from the lowest valid scientific value.
3. OAR surfaces shared one colour. OAR colours are now deterministic and collision-free within the scene.
4. TR converted both survivals to ordinary floating-point values before division. TR is now formed in log space and retains natural-log and log10 endpoints.
5. TCP returned a correct floating-point zero on probability underflow but did not label it explicitly. TCP now retains `ln(TCP)`, `log10(TCP)`, and a numerical-status field. Placeholder clonogen provenance such as `N/A` is rejected.

## Kernel findings

### 3.1A conventional LQ BED/EQD2

Implementation:

- `ascend/layer3/lq/basis.py`
- `ascend/layer3/lq/metrics.py`
- `ascend/layer3/lq/spatial.py`

Equations:

- `P(x) = sum_f d_f(x)`
- `Q(x) = sum_f d_f(x)^2`
- `BED(x) = P(x) + Q(x)/(alpha/beta)`
- `EQD2(x) = BED(x)/(1 + 2/(alpha/beta))`

Verified properties:

- Explicit fraction events use the direct sum of dose and squared dose.
- An identical-fraction total-dose shortcut uses `Q = D^2/n`, which is algebraically equivalent.
- BED/EQD2 are transformed voxelwise before ROI reduction.
- Geometry identity, dose hashes, mask hashes, fraction history, units, and alpha/beta assignments are gated.
- High-dose warnings do not switch the calculation to MLQ and do not change the LQ values.

Status: software-consistent. Scientific limitation: conventional LQ extrapolation at large fraction dose is retained only as a cautioned reference.

### 3.1B Guerrero–Li MLQ and EUD

Implementation:

- `ascend/layer3/response/mlq.py`
- `ascend/layer3/response/course.py`

Equations:

- `G(z) = 2(z + exp(-z) - 1)/z^2`
- `z = lambda*tau + delta*d`
- `K_f(x) = alpha*d_f(x) + beta*G(z_f(x))*d_f(x)^2`
- `SF(x) = exp(-sum_f K_f(x))`
- `K_T,eq = -ln(mean_GTV(SF))`
- EUD is the bounded numerical inverse of uniform-course MLQ effect under the explicit reference schedule.

Verified properties:

- `G(z)` uses a stable series near zero.
- Model parameters have no hidden numerical defaults.
- Dose, time, kinetic parameters, and survival domains are validated.
- Fraction effects accumulate before exponentiation.
- Mean survival uses `logsumexp`.
- EUD uses a bracketed Brent solver with a stored residual and tolerance.
- Tumour and normal-tissue MLQ fields are separate. A normal/OAR MLQ volume is materialised only when the declared normal kinetic parameter set is complete.

Status: software-consistent and provisional. Scientific limitation: parameters are scenario-based rather than patient-specific; the model does not produce NTCP, toxicity, immune, vascular, or non-local effects.

### 3.1C modelled therapeutic ratio

Implementation: `ascend/layer3/response/course.py`

Definition:

- `TR = mean_GTV[SF_N(actual heterogeneous course)] / SF_N(uniform tumour-isoeffective reference course)`
- `ln(TR) = log_mean_actual - log_reference`

Verified properties:

- Tumour EUD and a defined reference schedule are required.
- A complete and sourced normal-tissue MLQ parameter set is required.
- The ratio is now computed in log space.
- `ln(TR)` and `log10(TR)` remain available when ordinary floating-point conversion underflows or overflows.
- A near-unity display snap preserves the unsnapped ratio and unsnapped log result.

Status: kernel functioning. A zero, missing, or blocked display must be interpreted through `numerical_status` and `reason`, not changed to an arbitrary non-zero number.

### 3.1D direct-clonogenic Poisson TCP

Implementation: `ascend/layer3/tcp/service.py`

Equations:

- `mu_i = rho * V_voxel * SF_i * exp(phi_repopulation)`
- `N_surv = sum_i mu_i`
- `TCP = exp(-N_surv)`
- `ln(TCP) = -N_surv`

Verified properties:

- The kernel consumes the authoritative 3.1B tumour survival field and does not recalculate MLQ.
- Tumour mask, survival domain, voxel volume, density, provenance, and fraction-history linkage are gated.
- Spatial compartment burdens reconstruct the whole-tumour residual burden.
- TCP is grid-resolution invariant when tumour volume and survival are preserved.
- Repopulation is separately configured and monotonic.
- Numerical underflow is explicitly reported with retained `ln(TCP)` and `log10(TCP)`.
- `N/A`, `unknown`, and similar placeholder source identifiers no longer pass the clonogen provenance gate.

Status: software-consistent and biologically unvalidated. The direct-kill Poisson model assumes uniform clonogen density, independent subvolumes, and no non-local, immune, or vascular contribution.

## Latest case diagnosis

Audited result: `runs/all/derived/layer3_1/ASCEND_L3_1_20260825_052827_317591.json`

### TR

The stored TR is not a calculated zero. It is blocked:

- Reason: `INVALID_NORMAL_TISSUE_PARAMETER_SET`
- Missing: `delta_per_gy`, `repair_half_time`
- Normal scenario: N1 scenario values are present, but normal kinetics are not defined.

The code is behaving correctly by refusing to infer tumour repair kinetics for normal tissue. TR becomes applicable only after a defined normal kinetic preset or complete sourced custom normal parameter set is saved.

### TCP

The stored TCP calculation completed, but the direct probability underflowed:

- Mean tumour MLQ survival: `0.3174809636639057`
- Tumour EUD: `1.952633756321887 Gy`
- Expected surviving clonogens: `2938.0386835577856`
- `ln(TCP) = -2938.0386835577856`
- `log10(TCP) = -1275.9739878874404`
- Floating-point TCP: `0.0`, correctly interpreted as approximately zero by underflow, not as a failed kernel.

Residual-burden decomposition:

- Vertex: `0.0020469769` clonogens
- Valley: `6.2223610` clonogens
- Remainder: `2931.8142756` clonogens

The remainder contains about 99.788% of the modelled residual burden. The stored clonogen source and parameter-set ID are both `N/A`; this is not acceptable scientific provenance. The corrected gate blocks future runs until a non-placeholder source and parameter-set ID are supplied.

## 3D and OAR map contract

- Orthogonal biological slices are the default Layer 3.1 CAD mode; true VTK composite volume rendering remains an explicit mode.
- Surface, isosurface, and slice views remain explicit alternatives.
- Physical dose, s-BED, s-EQD2, tumour MLQ SF/effect, and normal-tissue MLQ SF/effect use the same validated DICOM patient-LPS geometry.
- OAR focus masks the full voxel field to the selected OAR for volumetric rendering.
- BED/EQD2 OAR display requires the matching identity-bound alpha/beta assignment.
- MLQ OAR display requires the separately computed normal-tissue MLQ field.
- A tumour MLQ field is never relabelled or substituted as an OAR response field.
- Every displayed OAR has a distinct deterministic colour. BODY/EXTERNAL contours remain excluded from the internal OAR scene to avoid enclosing-volume occlusion.

## Verification

Automated coverage includes closed-form LQ identities, explicit fraction histories, MLQ limiting behaviour, EUD inversion, TR unity and missing-parameter gates, TCP monotonicity and grid invariance, TCP underflow, rotated anisotropic LPS geometry, immutable volume handoff, volume/isosurface rendering, MLQ opacity semantics, distinct OAR colours, and normal-tissue MLQ materialisation.

Acceptance criterion: the complete test suite and lint checks must pass before the application is relaunched.
