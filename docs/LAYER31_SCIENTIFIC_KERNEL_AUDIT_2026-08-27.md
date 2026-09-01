# ASCEND 1.4.1 Layer 3.1 scientific-kernel revision audit

Date: 2026-08-27
Scope: tumour Guerrero–Li modified-LQ surviving fraction, survival-equivalent EUD, regional survivor decomposition, and their stored-result presentation.

## Conclusion

The Layer 3.1B tumour SF and EUD kernel has not drifted between ASCEND 1.3.5, 1.4.0, and 1.4.1. The same frozen synthetic case produces bit-identical Python `float` outputs in all three revisions. No scientific-kernel correction is required.

The reported difference observed in another case cannot be attributed to a revision change in these methods. SF and EUD change when the validated dose/fraction-event history, GTV mask, C1–C3 tumour scenario, kinetic parameter set, delivery time, or EUD reference schedule changes. Stored provenance must be compared before treating two displayed values as the same calculation.

The missing vertex/valley breakdown was a GUI regression. The contribution widget and stored regional records remained intact, but the enlarged embedded Map layout hid the viewer's internal regional page. ASCEND 1.4.1 now presents the same stored breakdown directly in visible Step 15.

## Method identity

- Course algorithm: `ASCEND-L3.1-fraction-event-course-v2.0`
- MLQ/EUD formalism: `ASCEND-L3.1B-fraction-event-MLQ-EUD-v2.0`
- Baselines: ASCEND 1.3.5 commit `941fc17`; ASCEND 1.4.0 commit `998b649`
- Candidate: ASCEND 1.4.1 current worktree
- Test case: `SYNTHETIC-CROSS-PLATFORM-V1`
- Reference parameters: alpha `0.3 Gy^-1`, beta `0.03 Gy^-2`, delta `0.02 Gy^-1`, repair half-time `0.5 h`, delivery time `0.2 h`

Source comparison confirms that `ascend/layer3/response/mlq.py` is unchanged across the three revisions. Changes in `course.py` add separately scoped OAR normal-tissue summaries and do not modify the tumour effect, SF aggregation, EUD inversion, or regional decomposition path.

## Audited equations and reductions

For each fraction event and voxel:

`z = lambda*tau + delta*d`

`G(z) = 2(z + exp(-z) - 1)/z^2`

`K_f(x) = alpha*d_f(x) + beta*G(z_f(x))*d_f(x)^2`

The course effect is accumulated before exponentiation:

`K(x) = sum_f K_f(x)` and `SF(x) = exp[-K(x)]`.

Whole-tumour mean SF is the arithmetic mean over the validated GTV, evaluated stably as:

`log(mean SF) = logsumexp[-K(x)] - log(N_GTV)`.

The equivalent log-survival effect is `K_T,eq = -log(mean SF)`. EUD is not mean dose. It is the bounded Brent-solver inverse of the uniform-course MLQ effect under the stored reference delivery schedule.

Regional vertex (`H`), valley (`V`), and remaining-GTV (`O`) records use disjoint validated masks. Their contribution endpoint is:

`phi_r = sum_{x in r} SF(x) / sum_{x in GTV} SF(x)`.

The audit requires `sum_r phi_r = 1` within numerical tolerance and requires the disjoint regional voxel counts to reconstruct the validated GTV.

## Revision comparison

| Revision | Mean tumour MLQ SF | Tumour EUD (Gy) | Absolute SF delta vs 1.4.1 | Absolute EUD delta vs 1.4.1 |
|---|---:|---:|---:|---:|
| ASCEND 1.3.5 (`941fc17`) | 0.11482093801025538 | 5.000769465414592 | 0 | 0 |
| ASCEND 1.4.0 (`998b649`) | 0.11482093801025538 | 5.000769465414592 | 0 | 0 |
| ASCEND 1.4.1 | 0.11482093801025538 | 5.000769465414592 | 0 | 0 |

The frozen repository expectation is SF `0.11482093801025538` with absolute tolerance `1e-12` and EUD `5.000769465414592 Gy` with absolute tolerance `1e-10`. The current values equal the expectations exactly.

## Completeness controls

- Fraction effects are accumulated in effect space; fraction doses are not averaged before MLQ evaluation.
- GTV SF uses every finite voxel in the validated GTV mask.
- `logsumexp` prevents premature survival underflow.
- EUD uses the explicit reference schedule and a bounded, residual-reporting Brent inversion.
- Tumour and normal-tissue parameters and fields remain separate.
- H/V/O survivor contributions are derived from the same authoritative tumour-survival field used for whole-tumour SF.
- GUI presenters consume stored results and do not recalculate SF, EUD, or regional contributions.
- Changes to the selected scenario, fraction events, mask identity, parameters, delivery time, or reference schedule represent different scientific inputs and can validly produce different outputs.

## Status

Software consistency: PASS.
Revision equivalence for the frozen case: PASS.
Clinical validation: NOT ESTABLISHED. Layer 3.1 remains computationally verified research software.
