from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidget

from ascend.gui.viewer_guidance import VIEWER_GUIDES, ViewerGuideDialog, guide_rows, show_viewer_guide


def test_every_requested_layer_has_a_complete_readable_viewer_guide() -> None:
    application = QApplication.instance() or QApplication([])
    required_terms = {
        "layer2_1": ("Hover over a vertex", "Average global FWHM", "Local FWHM"),
        "layer2_2": ("Background-corrected profile", "Saddle path", "Midpoint PVDR"),
        "individual_vertex_qa": ("Hover node", "Vertex selector", "OAR geometry"),
        "layer3_1": ("Displayed biological quantity / map tabs", "Metric strip", "3D mode"),
        "layer3_2": ("Stored field", "Absolute consequence surfaces", "Voxel probe Z / Y / X"),
    }
    assert set(VIEWER_GUIDES) == set(required_terms)
    for layer_key, terms in required_terms.items():
        rows = guide_rows(layer_key)
        controls = {control for control, _function in rows}
        assert len(rows) >= 15
        assert all(term in controls for term in terms)
        assert all(function.strip() for _control, function in rows)
        dialog = ViewerGuideDialog(layer_key)
        assert dialog.tabs.count() == len(VIEWER_GUIDES[layer_key][1])
        assert all(isinstance(dialog.tabs.widget(index), QTableWidget) for index in range(dialog.tabs.count()))
        dialog.close()
    application.processEvents()


def test_show_viewer_guide_reuses_one_nonmodal_dialog_per_layer() -> None:
    application = QApplication.instance() or QApplication([])
    parent = ViewerGuideDialog("layer2_1")
    first = show_viewer_guide(parent, "layer2_2")
    second = show_viewer_guide(parent, "layer2_2")
    assert first is second
    assert first.isVisible() and not first.isModal()
    parent.close(); application.processEvents()
