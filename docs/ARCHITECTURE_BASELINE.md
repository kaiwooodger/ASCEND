# ASCEND 0.6.x — Layer 3.1 conventional LQ workstation baseline

> Historical 0.6.x baseline only. The current implementation is documented in `RELEASE_1.5.0.md` and `LAYER31_FRACTION_EVENT_ENGINEERING_REPORT.md`.

Release version: 0.6.0.  
Validation scope: Validated through Layer 2.2.  
Baseline date: 2026-08-11 Australia/Sydney.

This checkpoint separates the Python scientific engine from all interface adapters. The native interface is PySide6/Qt 6. The optional browser interface and CLI use the same `ApplicationController`, `ASCENDCase`, Layer 1 service, Layer 2.1 service, Layer 2.2 service, status model, invalidation rules, and exporters.

## Frozen scientific sources

| Layer | Preserved source | SHA-256 |
|---|---|---|
| Layer 1 | `ascend/scientific/legacy/layer1_validated.py` | `dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490` |
| Layer 2.1 | `ascend/scientific/legacy/layer21_validated.py` | `4ddfa7eef71118db8edb40eba7331c3ee70a07021cd5386caf6f5f7c00cb3621` |
| Layer 2.2 | `ascend/scientific/legacy/layer22_validated.py` | `2a45da69f21428078ec227fb69e0175168f0528d39432bdc60a3724b313eeb24` |

The hashes equal the source-selection audit. None of these files changed during the Qt migration.

## Interface baseline

- Native GUI: `ascend/gui/main_window.py`, PySide6/Qt 6.
- Default launcher: `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 run_ascend.py` from the current ASCEND project directory.
- Optional web adapter: `python3 -m ascend.cli web-gui`.
- Batch interface: `python3 -m ascend.cli run ...`.
- GUI calculations: none. GUI actions call controller methods only.
- Worker execution: controller work runs through `QThreadPool`; the Qt event loop remains responsive.
- Layer 2.2 visual QA: centroid/edge projection, connected-component colours, node table, edge table, warnings, and provenance.
- Vertex provenance values: `explicit_rtstruct_vertices` or `connected_components_derived`.
- Layer 3.1: conventional voxelwise LQ BED/EQD2 using reusable fraction-history P/Q basis maps.
- Layer 3.2: plug-in interface only.

## Scientific baseline outcomes

The PHPROLRT01 case retains eight connected-component-derived nodes, five valid edges, zero excluded edges, and three graph components. `graph_disconnected` remains visible and the GUI now provides the required visual inspection surface.

The dedicated non-clinical 5V5 Layer-2.2B fixture completes through Layer 2.2 with five explicit RTSTRUCT vertices, four valid edges, zero excluded edges, one connected component, and `vertex_source=explicit_rtstruct_vertices`.

No clinical-use claim follows from either test case.
