"""Fresh workstation construction and complete active-case reset."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QMessageBox, QPushButton

from ascend.app.controller import ApplicationController
from ascend.gui.theme import workstation_stylesheet


class WorkstationLifecycleMixin:
    """Rebuild case-dependent UI state without carrying prior case evidence."""

    def _build_reset_button(self, header_layout: Any) -> None:
        self.reset_button = QPushButton("Reset / new case")
        self.reset_button.setToolTip(
            "Clear the active case, all case caches, inputs, results, viewers, and session provenance. "
            "Saved case files and exported reports are preserved."
        )
        self.reset_button.clicked.connect(self._reset_case)
        header_layout.addWidget(self.reset_button)

    def _initialise_workspace(self) -> None:
        """Construct a fresh controller and every case-dependent widget."""
        self.controller = ApplicationController()
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[Any] = set()
        self.layer22_viewer: Any = None
        self.layer22_viewer_run_id: str | None = None
        self.layer32_viewer: Any = None
        self.layer32_viewer_run_id: str | None = None
        self.layer31_viewer: Any = None
        self.layer31_viewer_run_id: str | None = None
        self._layer31_roi_entries: list[dict[str, Any]] = []
        self._layer31_component_entries: list[dict[str, Any]] = []
        self._loading_configuration = False
        # A reference may be selected before a DICOM case exists.  Retain it
        # across case construction so loading the new case configuration does
        # not erase the user's Import-page selection.
        self._pending_eclipse_reference: str | None = None
        dark = self.palette().color(QPalette.Window).lightness() < 128
        self.setStyleSheet(workstation_stylesheet(dark))
        self._build_shell()
        self._build_pages()
        self.navigation.setCurrentRow(1)
        self.refresh()

    def _reset_case(self) -> None:
        """Return the complete workstation to its initial new-case state."""
        if self._workers:
            return
        try:
            self.controller.reset_case()
        except Exception as exc:
            QMessageBox.critical(self, "ASCEND reset", str(exc))
            return
        for viewer in (self.layer22_viewer, self.layer31_viewer, self.layer32_viewer):
            if viewer is not None:
                scene = getattr(viewer, "scene", None)
                if scene is not None:
                    scene.close()
                viewer.close()
                viewer.deleteLater()
        previous = self.takeCentralWidget()
        self._initialise_workspace()
        if previous is not None:
            previous.deleteLater()
        self.navigation.setCurrentItem(self.navigation_items[0])
        self.footer_stage.setText("Case reset; import a new case")
