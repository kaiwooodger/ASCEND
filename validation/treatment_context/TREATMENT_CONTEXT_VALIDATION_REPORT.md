# ASCEND treatment-context validation

Overall status: **PASS**

This workstream validates what treatment component, dose object and prescription each metric belongs to. It is separate from numerical dose validation.

| Scenario | Dose context | Metric | Applicability | Interpretation | Status |
|---|---|---|---|---|---|
| sib_lrt | complete_single_plan | peripheral_coverage_v95_rxl | valid | protocol_interpretable | PASS |
| sib_lrt | complete_single_plan | high_dose_coverage_v95_rxh | valid | protocol_interpretable | PASS |
| sib_lrt | complete_single_plan | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| sib_lrt | complete_single_plan | mean_peak_dose | valid | protocol_interpretable | PASS |
| sib_lrt | complete_single_plan | mean_valley_dose | valid | protocol_interpretable | PASS |
| sib_lrt | complete_single_plan | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| sequential_lrt_boost | lrt_component | peripheral_coverage_v95_rxl | not_applicable | protocol_interpretable | PASS |
| sequential_lrt_boost | lrt_component | high_dose_coverage_v95_rxh | valid | protocol_interpretable | PASS |
| sequential_lrt_boost | lrt_component | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| sequential_lrt_boost | lrt_component | mean_peak_dose | valid | protocol_interpretable | PASS |
| sequential_lrt_boost | lrt_component | mean_valley_dose | valid | protocol_interpretable | PASS |
| sequential_lrt_boost | lrt_component | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| integrated_lrt_cert | complete_single_plan | peripheral_coverage_v95_rxl | valid | protocol_interpretable | PASS |
| integrated_lrt_cert | complete_single_plan | high_dose_coverage_v95_rxh | valid | protocol_interpretable | PASS |
| integrated_lrt_cert | complete_single_plan | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| integrated_lrt_cert | complete_single_plan | mean_peak_dose | valid | protocol_interpretable | PASS |
| integrated_lrt_cert | complete_single_plan | mean_valley_dose | valid | protocol_interpretable | PASS |
| integrated_lrt_cert | complete_single_plan | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| composite_course | composite_course | peripheral_coverage_v95_rxl | valid | protocol_interpretable | PASS |
| composite_course | composite_course | high_dose_coverage_v95_rxh | valid | protocol_interpretable | PASS |
| composite_course | composite_course | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| composite_course | composite_course | mean_peak_dose | valid | protocol_interpretable | PASS |
| composite_course | composite_course | mean_valley_dose | valid | protocol_interpretable | PASS |
| composite_course | composite_course | structure_based_dose_ratio | valid | provisional | PASS |
| composite_dose_with_boost_rx | composite_course | peripheral_coverage_v95_rxl | invalid | not_interpretable | PASS |
| composite_dose_with_boost_rx | composite_course | high_dose_coverage_v95_rxh | invalid | not_interpretable | PASS |
| composite_dose_with_boost_rx | composite_course | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| composite_dose_with_boost_rx | composite_course | mean_peak_dose | valid | protocol_interpretable | PASS |
| composite_dose_with_boost_rx | composite_course | mean_valley_dose | valid | protocol_interpretable | PASS |
| composite_dose_with_boost_rx | composite_course | structure_based_dose_ratio | valid | provisional | PASS |
| lrt_component_with_total_course_rx | lrt_component | peripheral_coverage_v95_rxl | invalid | not_interpretable | PASS |
| lrt_component_with_total_course_rx | lrt_component | high_dose_coverage_v95_rxh | invalid | not_interpretable | PASS |
| lrt_component_with_total_course_rx | lrt_component | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| lrt_component_with_total_course_rx | lrt_component | mean_peak_dose | valid | protocol_interpretable | PASS |
| lrt_component_with_total_course_rx | lrt_component | mean_valley_dose | valid | protocol_interpretable | PASS |
| lrt_component_with_total_course_rx | lrt_component | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| missing_rx_h | complete_single_plan | peripheral_coverage_v95_rxl | valid | protocol_interpretable | PASS |
| missing_rx_h | complete_single_plan | high_dose_coverage_v95_rxh | invalid | not_interpretable | PASS |
| missing_rx_h | complete_single_plan | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| missing_rx_h | complete_single_plan | mean_peak_dose | valid | protocol_interpretable | PASS |
| missing_rx_h | complete_single_plan | mean_valley_dose | valid | protocol_interpretable | PASS |
| missing_rx_h | complete_single_plan | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| missing_rx_l | complete_single_plan | peripheral_coverage_v95_rxl | invalid | not_interpretable | PASS |
| missing_rx_l | complete_single_plan | high_dose_coverage_v95_rxh | valid | protocol_interpretable | PASS |
| missing_rx_l | complete_single_plan | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| missing_rx_l | complete_single_plan | mean_peak_dose | valid | protocol_interpretable | PASS |
| missing_rx_l | complete_single_plan | mean_valley_dose | valid | protocol_interpretable | PASS |
| missing_rx_l | complete_single_plan | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| manual_prescriptions | complete_single_plan | peripheral_coverage_v95_rxl | valid | provisional | PASS |
| manual_prescriptions | complete_single_plan | high_dose_coverage_v95_rxh | valid | provisional | PASS |
| manual_prescriptions | complete_single_plan | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| manual_prescriptions | complete_single_plan | mean_peak_dose | valid | protocol_interpretable | PASS |
| manual_prescriptions | complete_single_plan | mean_valley_dose | valid | protocol_interpretable | PASS |
| manual_prescriptions | complete_single_plan | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| equal_peak_and_peripheral_rx | complete_single_plan | peripheral_coverage_v95_rxl | valid | provisional | PASS |
| equal_peak_and_peripheral_rx | complete_single_plan | high_dose_coverage_v95_rxh | valid | provisional | PASS |
| equal_peak_and_peripheral_rx | complete_single_plan | high_dose_volume_fraction | valid | protocol_interpretable | PASS |
| equal_peak_and_peripheral_rx | complete_single_plan | mean_peak_dose | valid | protocol_interpretable | PASS |
| equal_peak_and_peripheral_rx | complete_single_plan | mean_valley_dose | valid | protocol_interpretable | PASS |
| equal_peak_and_peripheral_rx | complete_single_plan | structure_based_dose_ratio | valid | protocol_interpretable | PASS |
| dose_uid_identity_conflict | lrt_component | peripheral_coverage_v95_rxl | invalid | not_interpretable | PASS |
| dose_uid_identity_conflict | lrt_component | high_dose_coverage_v95_rxh | invalid | not_interpretable | PASS |
| dose_uid_identity_conflict | lrt_component | high_dose_volume_fraction | invalid | not_interpretable | PASS |
| dose_uid_identity_conflict | lrt_component | mean_peak_dose | invalid | not_interpretable | PASS |
| dose_uid_identity_conflict | lrt_component | mean_valley_dose | invalid | not_interpretable | PASS |
| dose_uid_identity_conflict | lrt_component | structure_based_dose_ratio | invalid | not_interpretable | PASS |
| rtplan_component_identity_conflict | lrt_component | peripheral_coverage_v95_rxl | invalid | not_interpretable | PASS |
| rtplan_component_identity_conflict | lrt_component | high_dose_coverage_v95_rxh | invalid | not_interpretable | PASS |
| rtplan_component_identity_conflict | lrt_component | high_dose_volume_fraction | invalid | not_interpretable | PASS |
| rtplan_component_identity_conflict | lrt_component | mean_peak_dose | invalid | not_interpretable | PASS |
| rtplan_component_identity_conflict | lrt_component | mean_valley_dose | invalid | not_interpretable | PASS |
| rtplan_component_identity_conflict | lrt_component | structure_based_dose_ratio | invalid | not_interpretable | PASS |

## Component versus course interpretation

Synthetic LRT-component DR: **4.0**.
Synthetic composite-course DR: **1.6**.

The composite ratio is retained as a mathematical course-level result and carries `course_level_dr_not_comparable_to_lrt_component_dr`. Coverage calculations are suppressed when prescription and dose contexts conflict.

Fraction counts and dose per fraction remain component-specific. Components are not flattened into one total-dose/total-fraction pair.
