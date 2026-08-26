from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module(relative_path: str) -> tuple[Path, ast.Module]:
    path = ROOT / relative_path
    return path, ast.parse(path.read_text(encoding="utf-8"))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def _method_metrics(class_node: ast.ClassDef) -> dict[str, tuple[int, int]]:
    metrics: dict[str, tuple[int, int]] = {}
    branch_nodes = (ast.If, ast.For, ast.While, ast.Try, ast.BoolOp, ast.IfExp, ast.comprehension)
    for node in class_node.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        span = int(node.end_lineno or node.lineno) - node.lineno + 1
        branches = sum(isinstance(child, branch_nodes) for child in ast.walk(node))
        metrics[node.name] = span, branches
    return metrics


def test_main_window_is_a_cohesive_orchestration_shell() -> None:
    path, tree = _module("ascend/gui/main_window.py")
    main_window = _class(tree, "MainWindow")
    bases = {ast.unparse(base) for base in main_window.bases}
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 600
    assert {
        "WorkstationCasePagesMixin",
        "WorkstationPhysicalPagesMixin",
        "WorkstationBiologyPagesMixin",
        "WorkstationOutputPagesMixin",
        "WorkstationConfigurationMixin",
        "WorkstationLayer31Mixin",
        "WorkstationRefreshMixin",
    } <= bases
    assert max(span for span, _branches in _method_metrics(main_window).values()) <= 100


def test_layer31_viewer_has_no_bumpy_or_multi_responsibility_method() -> None:
    path, tree = _module("ascend/gui/layer31_viewer.py")
    viewer = _class(tree, "Layer31Viewer")
    metrics = _method_metrics(viewer)
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 550
    assert "Layer31CadMixin" in {ast.unparse(base) for base in viewer.bases}
    assert metrics["__init__"][0] <= 25
    assert max(span for span, _branches in metrics.values()) <= 30
    assert max(branches for _span, branches in metrics.values()) <= 12


def test_mesh_result_presentation_is_flattened_into_helpers() -> None:
    _path, tree = _module("ascend/gui/layer31_viewer_cad.py")
    metrics = _method_metrics(_class(tree, "Layer31CadMixin"))
    assert metrics["_apply_mesh_result"][0] <= 25
    assert metrics["_apply_mesh_result"][1] <= 10
