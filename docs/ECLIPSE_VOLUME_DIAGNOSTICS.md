# Eclipse Structure-Volume Diagnostics

The `diagnose-eclipse-volumes` command investigates the preserved PHPROLRT01 `all_vertices` and `all_valleys` formal volume failures. It is an analysis-only validation pathway.

It reads:

- the formal Eclipse comparison records;
- the referenced stored Layer 1 result;
- the selected RTSTRUCT, RTDOSE and CT series;
- the original Eclipse cumulative-DVH text exports.

It reports contour-stack, CT-voxelised and RTDOSE-sampled volumes separately. The formal comparator remains the stored contour-stack volume. It does not replace that comparator with the representation that agrees best.

The pathway reconstructs the selected masks with the existing locked half-open XOR rasteriser and nearest-neighbour transfer primitives. It checks stored dose masks for bitwise identity and performs two independent CT reconstructions because Layer 1 stores the CT-grid volume but not the CT-grid mask array.

Run:

```bash
cd /path/to/ASCEND_PROJECT
PYTHONPATH=. /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 -m ascend.cli \
  diagnose-eclipse-volumes \
  --case runs/all/ascend_case.json \
  --comparison runs/all/validation/eclipse_dvh/results/eclipse_dvh_comparisons.json \
  --output runs/all/validation/eclipse_dvh/volume_diagnostics
```

The command returns exit code `4` because the original software-agreement failures remain preserved after investigation. Exit code `2` indicates the diagnostic could not be constructed.

No CERR evidence is required or fabricated. No acceptance criterion, formal comparison record, Layer 1 result, or locked scientific source is modified.
