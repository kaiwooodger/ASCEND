# ASCEND 1.6.6

Release identifier: `ASCEND-1.6.6-CONTROL-POINT-BEAM-ON-TIME-20260909`

ASCEND 1.6.6 replaces the former single-dose-rate beam-on estimate with an auditable integration of the RTPLAN control points.

## Calculation contract

For each referenced treatment beam, ASCEND reads `BeamMeterset` from `FractionGroupSequence.ReferencedBeamSequence` and the beam's ordered `ControlPointSequence`. For consecutive control points `i` and `i+1`:

```text
interval_mu = BeamMeterset
              × (CMW[i+1] - CMW[i])
              / FinalCumulativeMetersetWeight

interval_seconds = interval_mu / DoseRateSet[i] × 60

beam_on_time_seconds = sum(interval_seconds)
```

`DoseRateSet[i]` is the explicit or inherited dose rate active at the starting control point. The terminal control point has no following interval, so its dose rate does not contribute.

## Validation and failure states

The calculation is published only when all required RTPLAN evidence is coherent:

- referenced `BeamMeterset` is present, finite, non-negative, and expressed in MU;
- at least two ordered control points exist and declared/actual counts agree;
- cumulative meterset weights are finite, begin at zero, increase monotonically, and end at the declared final weight;
- every positive-MU interval has a finite positive start-control-point dose rate.

Failure leaves beam-on time unset and records a machine-readable reason. ASCEND does not substitute an end-control-point rate or publish a partial total.

## Stored evidence

RTPLAN delivery metadata schema `ASCEND-RTPLAN-delivery-v2` records:

- each control point's index, cumulative meterset weight, cumulative MU, effective dose rate, and dose-rate source index;
- each interval's start/end indices, meterset-weight increment, MU increment, applied dose rate, and calculated seconds;
- per-beam, per-fraction-group, per-fraction, and planned-course totals when complete.

The pre-1.6.6 `estimated_*` fields remain compatibility aliases for one release series. The workstation labels the value `Control-point beam-on`.

## Interpretation boundary

This is a planned beam-radiation time derived from RTPLAN metadata. It excludes imaging, setup, inter-beam delays, and mechanical-transition overhead. It is not a delivered treatment time from a treatment record.

## Unchanged scope

The 1.6.5 Layer 1/Layer 2.1/Layer 3.1C anatomical-mask ownership and exact-identity contract remains intact. Physical dose, Layer 2 metrics, Layer 3 biological calculations, and locked validation definitions are unchanged.

## Verification

Regression coverage includes constant and variable dose rates, inherited `DoseRateSet`, final-control-point rate exclusion, missing start-point rate, control-point metadata serialization, fraction-group totals, and planned-course totals.
