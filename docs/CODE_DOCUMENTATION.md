# ASCEND Code Documentation Baseline

## Scope

ASCEND 1.5.0 production code is documented at three levels:

1. Every unlocked Python module has a module contract.
2. Every public Python class, function, and method has an interface docstring.
3. Safety-critical implementation boundaries have rationale comments explaining
   decisions that cannot be inferred safely from syntax alone.

The optional browser client also contains boundary comments. It gathers inputs
and presents stored controller results; it does not implement scientific
calculations.

## Architectural boundaries

| Area | Responsibility | Explicit exclusion |
|---|---|---|
| `ascend/app` | Case workflow, configuration, invalidation, service dispatch | Scientific calculations |
| `ascend/dicom` | Header discovery, UID chains, ROI identity, strict geometry | Clinical interpretation |
| `ascend/layer1` | Validated-input preparation, provenance, cache, atomic publication | Reimplementation of locked Layer 1 science |
| `ascend/layer2/metrics` | Layer 2.1 handoff, applicability, optional QA evidence | GUI-side metric calculation |
| `ascend/layer2/graph` | Layer 2.2 scope enforcement and stored graph evidence | Interpolation or unsupported-grid claims |
| `ascend/layer3` | Gated fraction history, spatial LQ, MLQ survival/EUD, TR, field storage, and separate Layer 3.2 services | Clinical outcome prediction or GUI-side calculation |
| `ascend/gui` | Native Qt input and physical evidence presentation | Dose, DVH, PVDR, or biological calculation |
| `ascend/web` | Optional localhost controller adapter | Independent scientific logic |
| `ascend/validation` | Independent verification and comparison workstreams | Mutation of validated results |

## Locked scientific source policy

The validated scientific sources in `ascend/scientific/legacy` remain
byte-identical. Comments and docstrings were not inserted into those files
because any edit would invalidate their locked SHA-256 provenance. Their role,
inputs, outputs, and calling boundaries are documented in the unlocked service
modules and existing architecture/validation documents.

The protected sources are:

- `layer1_validated.py`
- `layer21_validated.py`
- `layer22_validated.py`
- `layer22_reference_validated.py`

## Commenting standard

Comments explain contracts, provenance, failure semantics, and non-obvious
scientific or DICOM constraints. They do not narrate individual assignments or
repeat the code. This avoids stale line-by-line commentary while preserving the
information needed to audit and maintain the system.

Critical documented invariants include:

- ROI names are migration/display metadata; RTSTRUCT SOP UID plus ROI number is
  authoritative.
- DICOM-chain validity and chain selection are independent states.
- RTDOSE frame offsets are never sorted independently from pixel frames.
- Intentionally unselected ROIs do not generate invented metric rows.
- Cache artifacts are hash-verified and materialised without hard links.
- Formal runs and cache entries are atomically published.
- Layer 2.2 unvalidated spacing is outside scope, not invalid DICOM geometry.
- Layer 3.1 contains no active scientific implementation or result migration.
- Layer 3.2 fails closed while the replacement Layer 3.1 contract is undefined.
- GUI and browser code present stored results and never calculate science.

## Coverage maintenance

Run the deterministic documentation audit/fill utility after adding unlocked
Python modules or public interfaces:

```bash
python3 tools/document_python_interfaces.py
```

The utility excludes `ascend/scientific/legacy` by design. Generated baseline
docstrings must be replaced with domain-specific contracts when an interface has
scientific, safety, provenance, or workflow significance.

## Verification

Documentation-only releases require:

```bash
python3 -m compileall -q ascend benchmarks run_ascend.py tools
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -t .
```

The locked scientific hashes must also match the values enforced by the source
integrity regression tests.
