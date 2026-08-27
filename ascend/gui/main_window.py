"""Native Qt Widgets workstation that presents controller state without performing scientific calculations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ascend import __validation_scope__, __version__
from ascend.app.controller import ApplicationController
from ascend.gui.theme import StatusPill, workstation_stylesheet
from ascend.gui.screen_layout import show_maximised_on_current_screen
from ascend.gui.workstation_biology_pages import WorkstationBiologyPagesMixin
from ascend.gui.workstation_case_pages import WorkstationCasePagesMixin
from ascend.gui.workstation_configuration import WorkstationConfigurationMixin
from ascend.gui.workstation_layer31_configuration import WorkstationLayer31Mixin
from ascend.gui.workstation_output_pages import WorkstationOutputPagesMixin
from ascend.gui.workstation_page_builders import WorkstationPageBuilderMixin
from ascend.gui.workstation_physical_pages import WorkstationPhysicalPagesMixin
from ascend.gui.workstation_refresh import WorkstationRefreshMixin
from ascend.gui.workstation_widgets import (
    GraphCanvas,
    supporting_output_rows,
)
from ascend.gui.workstation_widgets import (
    heading as _heading,
)
from ascend.models.case import ASCENDCase

__all__ = ["GraphCanvas", "MainWindow", "launch", "supporting_output_rows"]


APPLICATION_DISPLAY_NAME = "ASCEND"
APPLICATION_ICON_PATH = Path(__file__).with_name("assets") / "ascend_icon.png"

# GUI boundary rule: widgets render and edit controller-owned state.  They never
# calculate dose, DVH, geometry, PVDR, BED, or EQD2 values.  Worker calls below
# invoke controller services and repaint only after a stored result is returned.

# Set identity before QApplication construction so macOS attributes the process
# and dock/window icon to ASCEND rather than the Python interpreter.
QCoreApplication.setApplicationName(APPLICATION_DISPLAY_NAME)
QCoreApplication.setApplicationVersion(__version__)
QCoreApplication.setOrganizationName(APPLICATION_DISPLAY_NAME)


def application_icon() -> QIcon:
    """Load the packaged ASCEND workstation icon."""
    return QIcon(str(APPLICATION_ICON_PATH))


def configure_application_identity(application: QApplication) -> None:
    """Set application metadata before creating the main window."""
    application.setApplicationName(APPLICATION_DISPLAY_NAME)
    application.setApplicationDisplayName(APPLICATION_DISPLAY_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName(APPLICATION_DISPLAY_NAME)
    application.setDesktopFileName("ASCEND")
    icon = application_icon()
    if not icon.isNull():
        application.setWindowIcon(icon)


STAGES = (
    ("CASE", "1. Import"),
    ("CASE", "2. Case configuration"),
    ("CASE", "3. Structure-role mapping"),
    ("CASE", "4. Layer 1 validation"),
    ("PHYSICAL", "5. Layer 2.1 LRT metrics"),
    ("PHYSICAL", "6. Layer 2.2 Spatial PVDR"),
    ("BIOLOGICAL", "7. Layer 3.1 Radiobiology"),
    ("BIOLOGICAL", "8. Layer 3.2 Biological modelling"),
    ("OUTPUT", "9. Review"),
    ("OUTPUT", "10. Export"),
)


class WorkerSignals(QObject):
    """Carry background controller completion or controlled error to the UI."""

    finished = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    """Run a controller operation outside the Qt event loop."""

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self) -> None:
        """Execute the supplied controller operation and emit its result."""
        try:
            self.signals.finished.emit(self.function())
        except Exception as exc:
            self.signals.error.emit(str(exc))


class MainWindow(
    WorkstationPageBuilderMixin,
    WorkstationCasePagesMixin,
    WorkstationPhysicalPagesMixin,
    WorkstationBiologyPagesMixin,
    WorkstationOutputPagesMixin,
    WorkstationConfigurationMixin,
    WorkstationLayer31Mixin,
    WorkstationRefreshMixin,
    QMainWindow,
):
    """Represent main window state and behavior."""

    def __init__(self) -> None:
        super().__init__()
        application = QApplication.instance()
        if application is not None:
            configure_application_identity(application)
        self.setWindowTitle(f"ASCEND {__version__} — LRT Analysis Workstation")
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1420, 900)
        self.controller = ApplicationController()
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self.layer22_viewer: Any = None
        self.layer22_viewer_run_id: str | None = None
        self.layer32_viewer: Any = None
        self.layer32_viewer_run_id: str | None = None
        self.layer31_viewer: Any = None
        self.layer31_viewer_window: Any = None
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

    def _build_shell(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("topBar")
        header.setFixedHeight(66)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 8, 18, 8)
        header_layout.setSpacing(16)
        brand = QLabel(f"ASCEND {__version__}")
        brand.setObjectName("brand")
        header_layout.addWidget(brand)
        case_block = QVBoxLayout()
        case_block.setSpacing(1)
        self.header_case = QLabel("No case open")
        self.header_case.setObjectName("caseName")
        self.header_status = QLabel("TPS —  ·  Dose —")
        self.header_status.setObjectName("caseContext")
        case_block.addWidget(self.header_case)
        case_block.addWidget(self.header_status)
        header_layout.addLayout(case_block, 1)
        self.header_layer1 = StatusPill("NOT RUN")
        self.header_layer21 = StatusPill("NOT RUN")
        self.header_layer22 = StatusPill("NOT RUN")
        self.header_interpretation = StatusPill("NOT RUN")
        for label, pill in (
            ("L1", self.header_layer1),
            ("L2.1", self.header_layer21),
            ("L2.2", self.header_layer22),
            ("INTERP", self.header_interpretation),
        ):
            block = QVBoxLayout()
            block.setSpacing(1)
            caption = QLabel(label)
            caption.setObjectName("caseContext")
            caption.setAlignment(Qt.AlignCenter)
            block.addWidget(caption)
            block.addWidget(pill)
            header_layout.addLayout(block)
        self.activity = StatusPill("PASS")
        self.activity.setText("READY")
        header_layout.addWidget(self.activity)
        outer.addWidget(header)
        splitter = QSplitter()
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(8, 12, 8, 10)
        side_layout.setSpacing(4)
        nav_title = QLabel("WORKFLOW")
        nav_title.setObjectName("navigationTitle")
        side_layout.addWidget(nav_title)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation_items: dict[int, QListWidgetItem] = {}
        self.navigation_labels: dict[int, str] = {}
        current_group = None
        page_index = 0
        for group, label in STAGES:
            if group != current_group:
                item = QListWidgetItem(group)
                item.setFlags(Qt.NoItemFlags)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                self.navigation.addItem(item)
                current_group = group
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, page_index)
            self.navigation.addItem(item)
            self.navigation_items[page_index] = item
            self.navigation_labels[page_index] = label
            page_index += 1
        self.navigation.currentItemChanged.connect(self._navigate)
        side_layout.addWidget(self.navigation)
        self.sidebar_case = QLabel("No case open\nLayer 1 not run")
        self.sidebar_case.setObjectName("sidebarCase")
        self.sidebar_case.setWordWrap(True)
        side_layout.addWidget(self.sidebar_case)
        splitter.addWidget(sidebar)
        self.pages = QStackedWidget()
        splitter.addWidget(self.pages)
        splitter.setSizes([270, 1150])
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)
        self.setCentralWidget(central)
        status = QStatusBar()
        self.footer_stage = QLabel("Ready")
        self.footer_version = QLabel(f"ASCEND {__version__} · {__validation_scope__}")
        self.footer_run = QLabel("Run —")
        status.addWidget(self.footer_stage, 1)
        status.addPermanentWidget(self.footer_version)
        status.addPermanentWidget(self.footer_run)
        self.setStatusBar(status)

    def _new_page(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        first, second = _heading(title, subtitle)
        layout.addWidget(first)
        if subtitle:
            layout.addWidget(second)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return page, layout

    @staticmethod
    def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        if description:
            detail = QLabel(description)
            detail.setObjectName("sectionDescription")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        return frame, layout

    def _navigate(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        index = current.data(Qt.UserRole)
        if index is None:
            return
        self.pages.setCurrentIndex(int(index))
        self.refresh()

    def _browse_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select DICOM case directory")
        if path:
            self.source_path.setText(path)

    def _browse_tps_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Eclipse DVH reference",
            filter="Eclipse DVH or ASCEND metrics (*.txt *.csv);;Text files (*.txt);;CSV files (*.csv)",
        )
        if path:
            self.tps_csv.setText(path)
            self._pending_eclipse_reference = path
            if self.controller.case:
                self.eclipse_import_status.setText(f"Selected Eclipse reference: {path}")
                self._prefill_protocol_endpoints()
            else:
                self.eclipse_import_status.setText(f"Selected Eclipse reference: {path}. Endpoint mapping will run after DICOM import.")

    def _browse_tps_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select folder containing Eclipse DVH text exports")
        if path:
            self.tps_csv.setText(path)
            self._pending_eclipse_reference = path
            if self.controller.case:
                self.eclipse_import_status.setText(f"Selected Eclipse reference folder: {path}")
                self._prefill_protocol_endpoints()
            else:
                self.eclipse_import_status.setText(
                    f"Selected Eclipse reference folder: {path}. Endpoint mapping will run after DICOM import."
                )

    def _import_case(self) -> None:
        source = self.source_path.text().strip()
        if not source:
            QMessageBox.critical(self, "ASCEND", "Select a DICOM case directory.")
            return
        selected_reference = self.tps_csv.text().strip()
        if selected_reference:
            self._pending_eclipse_reference = selected_reference
        project = Path(__file__).resolve().parents[2]
        case_root = project / "runs" / Path(source).name
        self._work(lambda: self.controller.import_case(source, case_root), self._after_case_loaded)

    def _select_dicom_chain(self) -> None:
        chain_id = self.chain_select.currentData()
        if not chain_id:
            QMessageBox.critical(self, "ASCEND", "No DICOM chain is available for selection.")
            return
        reason = self.chain_override_reason.text().strip()
        self._work(
            lambda: self.controller.select_dicom_chain(str(chain_id), bool(reason), reason or None),
            self._after_case_loaded,
        )

    def _inspect_layer1_cache(self) -> None:
        if not self.controller.case:
            QMessageBox.critical(self, "ASCEND", "Open a case first.")
            return
        self.import_summary.setPlainText(json.dumps(self.controller.inspect_layer1_cache(), indent=2))

    def _clear_layer1_cache(self) -> None:
        if not self.controller.case:
            QMessageBox.critical(self, "ASCEND", "Open a case first.")
            return
        if QMessageBox.question(self, "Clear Layer 1 cache", "Remove all reusable Layer 1 cache entries for this case?") != QMessageBox.Yes:
            return
        removed = self.controller.clear_layer1_cache(confirmed=True)
        self.import_summary.setPlainText(json.dumps({"removed_entries": removed}, indent=2))

    def _open_case(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open ASCEND case", filter="ASCEND case (ascend_case.json);;JSON (*.json)")
        if not path:
            return
        try:
            # Opening an existing case uses its own persisted reference and must
            # not inherit a path displayed for a previously open case.
            self._pending_eclipse_reference = None
            self.tps_csv.clear()
            self.controller = ApplicationController(ASCENDCase.load(path))
            self._after_case_loaded(None)
        except Exception as exc:
            QMessageBox.critical(self, "ASCEND", str(exc))

    def _after_case_loaded(self, _value: Any) -> None:
        pending_reference = self._pending_eclipse_reference or self.tps_csv.text().strip() or None
        self._load_configuration()
        self._load_role_options()
        case = self.controller.case
        if case and pending_reference and not case.configuration.tps_metrics_csv:
            self.tps_csv.setText(pending_reference)
            self.eclipse_import_status.setText(f"Selected Eclipse reference: {pending_reference}")
            if self._prefill_protocol_endpoints(silent=True):
                self._pending_eclipse_reference = None
        self.refresh()

    def _run_layer1(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_layer1)

    def _run_layer21(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_layer21)

    def _run_layer22(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_layer22)

    def _run_layer32(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_layer32)

    def _build_layer22_visualization(self) -> None:
        case = self.controller.case
        if not case or not case.layer2_2.result:
            QMessageBox.critical(self, "ASCEND", "Run Layer 2.2 before building the 3D viewer.")
            return
        self.layer22_viewer_status.setText("Building hash-verified GTV and vertex surfaces from the Layer 1 native archive…")
        from ascend.gui.layer22_viewer import prepare_layer22_viewer_data

        self._work(lambda: prepare_layer22_viewer_data(case), self._show_layer22_visualization)

    def _show_layer22_visualization(self, data: Any) -> None:
        if self.layer22_viewer is None:
            from ascend.gui.layer22_viewer import Layer22Viewer

            self.layer22_viewer = Layer22Viewer()
            self.layer22_viewer_layout.insertWidget(1, self.layer22_viewer, 1)
        self.layer22_viewer.set_data(data)
        self.layer22_viewer.setEnabled(True)
        self.layer22_viewer_run_id = self.controller.case.layer2_2.run_id if self.controller.case else None
        self.layer22_viewer_status.setText(f"Rendered from Layer 1 validated masks and native RTDOSE. Vertex source: {data.vertex_source}.")
        self.layer22_display_tabs.setCurrentIndex(1)

    def _build_layer32_visualization(self) -> None:
        case = self.controller.case
        if case and not case.configuration.layer32_enabled:
            QMessageBox.information(self, "ASCEND Layer 3.2", "Layer 3.2 is disabled for this case.")
            return
        if not case or not case.layer3_2.result:
            QMessageBox.critical(self, "ASCEND Layer 3.2", "Run Layer 3.2 before building the biological field viewer.")
            return
        self.layer32_viewer_status.setText("Loading the hash-verified stored Layer 3.2 field archive…")
        from ascend.gui.layer32_viewer import prepare_layer32_viewer_data

        self._work(lambda: prepare_layer32_viewer_data(case), self._show_layer32_visualization)

    def _show_layer32_visualization(self, data: Any) -> None:
        if self.layer32_viewer is None:
            from ascend.gui.layer32_viewer import Layer32Viewer

            self.layer32_viewer = Layer32Viewer()
            self.layer32_viewer_layout.insertWidget(2, self.layer32_viewer, 1)
        self.layer32_viewer.set_data(data)
        self.layer32_viewer.setEnabled(True)
        self.layer32_viewer_run_id = self.controller.case.layer3_2.run_id if self.controller.case else None
        self.layer32_viewer_status.setText(
            "Showing hash-verified 3D scalar shells and 2D planes in DICOM patient LPS coordinates. "
            "GTV is yellow, vertices are cyan, configured OARs are magenta, and the wireframe marks the model crop."
        )

    def _run_physical(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_physical_analysis)

    def _export(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.export, self._show_exports)

    def _export_supporting_outputs_json(self) -> None:
        if not self._current_supporting_outputs:
            QMessageBox.information(self, "ASCEND", "No stored supporting outputs are available to export.")
            return
        case = self.controller.case
        case_id = case.case_id if case else "case"
        run_id_value = case.layer2_1.run_id if case else None
        default_name = f"ASCEND_{case_id}_{run_id_value or 'Layer2_1'}_supporting_outputs.json"
        default_directory = case.root / "exports" if case else Path.cwd()
        default_directory.mkdir(parents=True, exist_ok=True)
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export Layer 2.1 supporting outputs",
            str(default_directory / default_name),
            "JSON files (*.json)",
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            path.write_text(json.dumps(self._current_supporting_outputs, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "ASCEND", f"Supporting-output export failed: {exc}")
            return
        self.footer_stage.setText(f"Supporting outputs exported: {path.name}")

    def _show_exports(self, paths: Any) -> None:
        self.export_result.setPlainText("\n".join(str(path) for path in paths))
        self.refresh()

    def _work(self, operation: Callable[[], Any], finished: Callable[[Any], None] | None = None) -> None:
        self.activity.set_status("WARN")
        self.activity.setText("WORKING")
        self.footer_stage.setText("Calculation in progress")
        self.navigation.setEnabled(False)
        worker = Worker(operation)
        self._workers.add(worker)

        def done(value: Any) -> None:
            self._workers.discard(worker)
            self.navigation.setEnabled(True)
            self.activity.set_status("PASS")
            self.activity.setText("READY")
            self.footer_stage.setText("Ready")
            if finished:
                finished(value)
            self.refresh()

        def error(message: str) -> None:
            self._workers.discard(worker)
            self.navigation.setEnabled(True)
            self.activity.set_status("BLOCKED")
            self.activity.setText("ERROR")
            self.footer_stage.setText("Operation failed")
            QMessageBox.critical(self, "ASCEND", message)
            self.refresh()

        worker.signals.finished.connect(done)
        worker.signals.error.connect(error)
        self.thread_pool.start(worker)


def launch() -> None:
    """Handle launch for the enclosing ASCEND workflow."""
    QCoreApplication.setApplicationName(APPLICATION_DISPLAY_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    QCoreApplication.setOrganizationName(APPLICATION_DISPLAY_NAME)
    application = QApplication.instance()
    if application is None:
        arguments = list(sys.argv)
        if arguments:
            arguments[0] = APPLICATION_DISPLAY_NAME
        application = QApplication(arguments)
    configure_application_identity(application)
    window = MainWindow()
    # The physical, CAD, and biological viewers are embedded in this shell.
    # Give them the complete available monitor area on every desktop OS.
    show_maximised_on_current_screen(window)
    raise SystemExit(application.exec())


if __name__ == "__main__":
    launch()
