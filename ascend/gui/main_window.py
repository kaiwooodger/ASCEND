"""Native Qt Widgets workstation that presents controller state without performing scientific calculations."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable

import pydicom
from PySide6.QtCore import QCoreApplication, QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBox,
    QVBoxLayout,
    QWidget,
)

from ascend import __validation_scope__, __version__
from ascend.app.controller import ApplicationController
from ascend.models.case import ASCENDCase
from ascend.models.config import (
    DOSE_CONTEXTS,
    PROTOCOL_ENDPOINT_ROLES,
    OAR_CLASSIFICATIONS,
    PRESCRIPTION_SOURCES,
    TREATMENT_DELIVERY_MODES,
    TREATMENT_APPROACHES,
    CaseConfiguration,
    Prescription,
)
from ascend.layer3.nonlocal_effect.models import DEFAULT_PARAMETERS, resolved_parameters
from ascend.layer3.response.mlq import (
    NORMAL_KINETIC_PRESETS,
    NORMAL_SCENARIOS,
    SCENARIO_SOURCE,
    TUMOUR_KINETIC_PRESETS,
    TUMOUR_SCENARIOS,
)
from ascend.treatment.models import TreatmentComponent
from ascend.gui.theme import METRIC_LABELS, MetricCard, StatePanel, StatusPill, WarningBanner, canonical_state, workstation_stylesheet
from ascend.workflow.preferences import (
    DEFAULT_SUPPORTING_OUTPUT_CATEGORIES,
    normalise_vertex_records,
    protocol_endpoint_record,
    selected_supporting_outputs,
)


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


class WorkstationToolBox(QToolBox):
    """QToolBox with stable, unclipped section headers on macOS Qt styles."""

    TAB_HEIGHT = 38

    def addItem(self, widget: QWidget, text: str) -> int:  # type: ignore[override]
        """Handle add item for the enclosing ASCEND workflow."""
        index = super().addItem(widget, text)
        self.normalise_tab_buttons()
        return index

    def normalise_tab_buttons(self) -> None:
        """Normalize tab buttons without changing scientific meaning."""
        for button in self.findChildren(QAbstractButton):
            if button.metaObject().className() != "QToolBoxButton":
                continue
            button.setMinimumHeight(self.TAB_HEIGHT)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class GraphCanvas(QWidget):
    """Read-only 2-D projection of the stored Layer 2.2 graph result."""

    PALETTE = ("#2463a0", "#b35c1e", "#21835b", "#7a4ea3", "#a33b56")

    def __init__(self) -> None:
        super().__init__()
        self.result: dict[str, Any] | None = None
        self.projection = "auto"
        self.show_edge_labels = True
        self.show_invalid_edges = True
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self._drag_position: QPointF | None = None
        self.setMinimumSize(520, 400)
        self.setCursor(Qt.OpenHandCursor)

    def zoom_by(self, factor: float) -> None:
        self.zoom = float(min(max(self.zoom * factor, 0.5), 6.0)); self.update()

    def rotate_by(self, degrees: float) -> None:
        self.rotation_degrees = (self.rotation_degrees + float(degrees)) % 360.0; self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1/1.15); event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton: self._drag_position = event.position(); self.setCursor(Qt.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is not None:
            current = event.position(); self.pan += current - self._drag_position; self._drag_position = current; self.update(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton: self._drag_position = None; self.setCursor(Qt.OpenHandCursor); event.accept()

    def set_result(self, result: dict[str, Any] | None) -> None:
        """Update result presentation state."""
        self.result = result
        self.update()

    def set_projection(self, projection: str) -> None:
        """Update projection presentation state."""
        self.projection = projection
        self.update()

    def set_edge_labels_visible(self, visible: bool) -> None:
        """Update edge labels visible presentation state."""
        self.show_edge_labels = visible
        self.update()

    def set_invalid_edges_visible(self, visible: bool) -> None:
        """Update invalid edges visible presentation state."""
        self.show_invalid_edges = visible
        self.update()

    @staticmethod
    def _edge_label(edge: dict[str, Any]) -> str:
        edge_id = edge.get("edge_id", "?")
        value = edge.get("ipvdr")
        formatted = f"{float(value):.3f}" if isinstance(value, (int, float)) and math.isfinite(float(value)) else "—"
        return f"E{edge_id}  iPVDR {formatted}"

    @staticmethod
    def _components(names: list[str], edges: list[dict[str, Any]]) -> dict[str, int]:
        neighbours = {name: set() for name in names}
        for edge in edges:
            a, b = edge.get("nodes", [None, None])
            if a in neighbours and b in neighbours:
                neighbours[a].add(b)
                neighbours[b].add(a)
        output: dict[str, int] = {}
        component = 0
        for start in names:
            if start in output:
                continue
            pending = [start]
            output[start] = component
            while pending:
                current = pending.pop()
                for neighbour in neighbours[current]:
                    if neighbour not in output:
                        output[neighbour] = component
                        pending.append(neighbour)
            component += 1
        return output

    def paintEvent(self, _event: Any) -> None:
        """Handle paint event for the enclosing ASCEND workflow."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        background = self.palette().color(QPalette.Base)
        foreground = self.palette().color(QPalette.Text)
        muted = self.palette().color(QPalette.PlaceholderText)
        painter.fillRect(self.rect(), background)
        nodes = (self.result or {}).get("nodes", [])
        edges = (self.result or {}).get("edges", [])
        if not nodes:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignCenter, "Run Layer 2.2 to inspect centroids and edges")
            return
        painter.save()
        center = QPointF(self.rect().center())
        painter.translate(center + self.pan); painter.rotate(self.rotation_degrees); painter.scale(self.zoom, self.zoom); painter.translate(-center.x(), -center.y())
        coords = [list(map(float, item["centroid_lps_mm"])) for item in nodes]
        spreads = [max(row[axis] for row in coords) - min(row[axis] for row in coords) for axis in range(3)]
        axes = {
            "axial": [0, 1], "sagittal": [1, 2], "coronal": [0, 2],
        }.get(self.projection, sorted(range(3), key=lambda axis: spreads[axis], reverse=True)[:2])
        axis_labels = ("LPS X", "LPS Y", "LPS Z")
        xs = [row[axes[0]] for row in coords]
        ys = [row[axes[1]] for row in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(self.width() - 100, 1)
        height = max(self.height() - 100, 1)

        def point(index: int) -> QPointF:
            x = 50 + (xs[index] - min_x) / (max_x - min_x or 1.0) * width
            y = 50 + (max_y - ys[index]) / (max_y - min_y or 1.0) * height
            return QPointF(x, y)

        names = [str(item["node"]) for item in nodes]
        by_name = {name: index for index, name in enumerate(names)}
        components = self._components(names, edges)
        edge_labels: list[tuple[QPointF, str, QColor]] = []
        for edge in edges:
            if not edge.get("valid", False) and not self.show_invalid_edges:
                continue
            a, b = edge.get("nodes", [None, None])
            if a not in by_name or b not in by_name:
                continue
            color = QColor("#b42318" if not edge.get("valid", False) else "#627d98")
            painter.setPen(QPen(color, 2.0))
            first, second = point(by_name[a]), point(by_name[b])
            painter.drawLine(first, second)
            delta_x, delta_y = second.x() - first.x(), second.y() - first.y()
            length = math.hypot(delta_x, delta_y) or 1.0
            anchor = QPointF(
                (first.x() + second.x()) / 2.0 - 12.0 * delta_y / length,
                (first.y() + second.y()) / 2.0 + 12.0 * delta_x / length,
            )
            edge_labels.append((anchor, self._edge_label(edge), color))
        if self.show_edge_labels:
            edge_font = painter.font()
            edge_font.setPointSize(8)
            edge_font.setBold(True)
            painter.setFont(edge_font)
            for anchor, label, color in edge_labels:
                bounds = painter.fontMetrics().boundingRect(label)
                rect = QRectF(
                    anchor.x() - bounds.width() / 2.0 - 4.0,
                    anchor.y() - bounds.height() / 2.0 - 2.0,
                    bounds.width() + 8.0,
                    bounds.height() + 4.0,
                )
                painter.setPen(QPen(color, 1.0))
                painter.setBrush(background)
                painter.drawRoundedRect(rect, 3.0, 3.0)
                painter.drawText(rect, Qt.AlignCenter, label)
        font = painter.font()
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        for index, name in enumerate(names):
            p = point(index)
            color = QColor(self.PALETTE[components[name] % len(self.PALETTE)])
            painter.setPen(QPen(foreground, 1.0))
            painter.setBrush(color)
            painter.drawEllipse(p, 7, 7)
            label_x = 10 if p.x() < self.width() - 105 else -72
            label_y = -8 if p.y() > 32 else 19
            painter.drawText(p + QPointF(label_x, label_y), name)
        painter.restore(); painter.setPen(muted)
        painter.drawText(12, self.height() - 14, f"Projection: {axis_labels[axes[0]]} × {axis_labels[axes[1]]}; iPVDR labels; zoom {self.zoom:.2g}×; rotation {self.rotation_degrees:.0f}°")


def _heading(title: str, subtitle: str = "") -> tuple[QLabel, QLabel]:
    first = QLabel(title)
    first.setObjectName("title")
    second = QLabel(subtitle)
    second.setObjectName("subtitle")
    second.setWordWrap(True)
    return first, second


def _text_view() -> QTextEdit:
    widget = QTextEdit()
    widget.setReadOnly(True)
    widget.setFont(QFont("Menlo", 11))
    return widget


def _table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setAlternatingRowColors(True)
    widget.setWordWrap(True)
    widget.setShowGrid(False)
    widget.verticalHeader().setVisible(False)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def _set_table(widget: QTableWidget, rows: list[list[Any]], empty_message: str = "No records available.") -> None:
    widget.clearSpans()
    if not rows:
        widget.setRowCount(1)
        item = QTableWidgetItem(empty_message)
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor("#6b7785"))
        widget.setItem(0, 0, item)
        widget.setSpan(0, 0, 1, widget.columnCount())
        widget.setSelectionMode(QAbstractItemView.NoSelection)
        return
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            text = "" if value is None else str(value)
            widget.setItem(row_index, column_index, QTableWidgetItem(text))
    widget.resizeRowsToContents()


def _friendly_field_name(value: str) -> str:
    abbreviations = {
        "d50": "D50", "d95": "D95", "dmean": "Dmean", "dmax": "Dmax",
        "gtv": "GTV", "vtvh": "VTVH", "vtvl": "VTVL", "rxh": "RxH",
        "rxl": "RxL", "qa": "QA", "rtdose": "RTDOSE", "uid": "UID",
        "sha256": "SHA-256", "v95": "V95", "gy": "Gy", "cc": "cc",
        "mm": "mm", "pct": "%", "95pct": "95%",
    }
    words = []
    for word in str(value).replace("-", "_").split("_"):
        words.append(abbreviations.get(word.lower(), word.capitalize()))
    return " ".join(words)


def _supporting_unit(field_name: str) -> str:
    name = field_name.lower()
    if name.endswith("_gy"):
        return "Gy"
    if name.endswith("_cc"):
        return "cc"
    if name.endswith("_pct") or name.endswith("_percentage"):
        return "%"
    if name.endswith("_mm") or "spacing_mm" in name or "distance_mm" in name:
        return "mm"
    if name.endswith("_voxels") or name.endswith("_voxel_count") or name == "voxel_count":
        return "voxels"
    return ""


def _supporting_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value):
        return "; ".join(_supporting_value(item) for item in value) if value else "None"
    return str(value)


def supporting_output_rows(payload: dict[str, Any]) -> list[list[str]]:
    """Flatten stored supporting records for display without deriving scientific values."""
    rows: list[list[str]] = []
    context_keys = {"status", "applicability", "warnings", "reason"}

    def context(record: dict[str, Any]) -> str:
        parts = []
        for key in ("status", "applicability", "reason"):
            value = record.get(key)
            if value not in (None, "", []):
                parts.append(_supporting_value(value))
        warnings = record.get("warnings")
        if warnings:
            parts.append("Warnings: " + _supporting_value(warnings))
        return " · ".join(parts)

    def walk(section: str, path: list[str], value: Any, inherited_context: str = "") -> None:
        if isinstance(value, dict):
            current_context = context(value) or inherited_context
            ordinary_keys = [key for key in value if key not in context_keys]
            if not ordinary_keys:
                rows.append([section, " › ".join(path) or section, "—", "", current_context])
                return
            for key in ordinary_keys:
                walk(section, [*path, _friendly_field_name(key)], value[key], current_context)
            return
        if isinstance(value, list) and value and any(isinstance(item, (dict, list)) for item in value):
            for index, item in enumerate(value, 1):
                identity = None
                if isinstance(item, dict):
                    identity = next((item.get(key) for key in ("metric_id", "vertex_id", "oar_name", "endpoint_id", "id") if item.get(key)), None)
                label = _friendly_field_name(str(identity)) if identity is not None else f"Record {index}"
                walk(section, [*path, label], item, inherited_context)
            return
        field = path[-1] if path else section
        rows.append([
            section,
            " › ".join(path) if path else section,
            _supporting_value(value),
            _supporting_unit(field.replace(" ", "_")),
            inherited_context,
        ])

    for key, value in payload.items():
        section = _friendly_field_name(key)
        walk(section, [], value)
    return rows


class MainWindow(QMainWindow):
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
        for label, pill in (("L1", self.header_layer1), ("L2.1", self.header_layer21), ("L2.2", self.header_layer22), ("INTERP", self.header_interpretation)):
            block = QVBoxLayout(); block.setSpacing(1)
            caption = QLabel(label); caption.setObjectName("caseContext"); caption.setAlignment(Qt.AlignCenter)
            block.addWidget(caption); block.addWidget(pill)
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

    def _build_pages(self) -> None:
        self._build_import_page()
        self._build_configuration_page()
        self._build_mapping_page()
        self._build_layer1_page()
        self._build_layer21_page()
        self._build_layer22_page()
        self._build_layer31_page()
        self._build_layer32_page()
        self._build_review_page()
        self._build_export_page()

    def _build_import_page(self) -> None:
        _, layout = self._new_page("Import", "Header discovery only. Scientific calculations do not run during import.")
        row = QHBoxLayout()
        self.source_path = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_source)
        run = QPushButton("Import case")
        run.setObjectName("primary")
        run.clicked.connect(self._import_case)
        open_case = QPushButton("Open ASCEND case")
        open_case.clicked.connect(self._open_case)
        row.addWidget(self.source_path, 1)
        row.addWidget(browse)
        row.addWidget(run)
        row.addWidget(open_case)
        layout.addLayout(row)
        chain_explanation = QLabel(
            "DICOM chain selection chooses one UID-linked RTDOSE → RTPLAN → RTSTRUCT → planning-image series. "
            "It prevents dose, prescription, contours, and images from different plans or exports being combined. "
            "ASCEND selects automatically only when exactly one complete chain exists."
        )
        chain_explanation.setObjectName("sectionDescription")
        chain_explanation.setWordWrap(True)
        layout.addWidget(chain_explanation)
        chain_row = QHBoxLayout()
        self.chain_select = QComboBox()
        self.chain_override_reason = QLineEdit()
        self.chain_override_reason.setPlaceholderText("Reason required only for incomplete-reference override")
        select_chain_button = QPushButton("Select DICOM chain")
        select_chain_button.clicked.connect(self._select_dicom_chain)
        chain_row.addWidget(self.chain_select, 1)
        chain_row.addWidget(self.chain_override_reason, 1)
        chain_row.addWidget(select_chain_button)
        layout.addLayout(chain_row)
        self.chain_detail = QLabel("Import a DICOM directory to resolve treatment chains.")
        self.chain_detail.setObjectName("sectionDescription")
        self.chain_detail.setWordWrap(True)
        layout.addWidget(self.chain_detail)
        eclipse_card, eclipse_layout = self._card(
            "Eclipse DVH references",
            "Import Eclipse CSV/TXT reference data with the DICOM case. Supported protocol endpoint definitions can be auto-filled on Case configuration.",
        )
        self.tps_csv = QLineEdit()
        eclipse_row = QHBoxLayout()
        eclipse_row.addWidget(self.tps_csv, 1)
        tps_browse = QPushButton("Browse Eclipse file")
        tps_browse.clicked.connect(self._browse_tps_csv)
        tps_folder = QPushButton("Browse Eclipse folder")
        tps_folder.clicked.connect(self._browse_tps_folder)
        eclipse_row.addWidget(tps_browse)
        eclipse_row.addWidget(tps_folder)
        eclipse_layout.addLayout(eclipse_row)
        self.eclipse_import_status = QLabel("No Eclipse DVH reference selected.")
        self.eclipse_import_status.setObjectName("sectionDescription")
        self.eclipse_import_status.setWordWrap(True)
        eclipse_layout.addWidget(self.eclipse_import_status)
        layout.addWidget(eclipse_card)
        cache_row = QHBoxLayout()
        inspect_cache = QPushButton("Inspect Layer 1 cache")
        inspect_cache.clicked.connect(self._inspect_layer1_cache)
        clear_cache = QPushButton("Clear Layer 1 cache")
        clear_cache.clicked.connect(self._clear_layer1_cache)
        cache_row.addWidget(inspect_cache)
        cache_row.addWidget(clear_cache)
        cache_row.addStretch()
        layout.addLayout(cache_row)
        self.import_summary = _text_view()
        layout.addWidget(self.import_summary, 1)

    def _build_configuration_page(self) -> None:
        _, layout = self._new_page("Case configuration", "Treatment meaning, prescription values, and prescription provenance are explicit inputs. Dose maxima are never used as prescriptions.")
        form = QFormLayout()
        self.treatment_approach = QComboBox()
        for label, value in (
            ("LRT alone", "LRT_ALONE"),
            ("Sequential LRT + cERT", "LRT_SEQUENTIAL_CERT"),
            ("Integrated / SIB LRT", "LRT_INTEGRATED"),
            ("Unknown / not established", "UNKNOWN"),
        ):
            self.treatment_approach.addItem(label, value)
        self.mode = QComboBox(); self.mode.addItems(TREATMENT_DELIVERY_MODES)
        self.dose_context = QComboBox(); self.dose_context.addItems(DOSE_CONTEXTS)
        self.rx_l = QComboBox(); self.rx_l.setEditable(True); self.rx_l.lineEdit().setPlaceholderText("Required for peripheral V95")
        self.rx_h = QComboBox(); self.rx_h.setEditable(True); self.rx_h.lineEdit().setPlaceholderText("Required for high-dose V95")
        self.rx_l_source = QComboBox(); self.rx_l_source.addItems(PRESCRIPTION_SOURCES)
        self.rx_h_source = QComboBox(); self.rx_h_source.addItems(PRESCRIPTION_SOURCES)
        self.fractions = QComboBox(); self.fractions.setEditable(True)
        self.protocol_id = QLineEdit()
        endpoint_widget = QWidget(); endpoint_layout = QVBoxLayout(endpoint_widget)
        endpoint_layout.setContentsMargins(0, 0, 0, 0); endpoint_layout.setSpacing(7)
        endpoint_selectors = QHBoxLayout()
        self.protocol_endpoint_role = QComboBox(); self.protocol_endpoint_role.addItems(PROTOCOL_ENDPOINT_ROLES)
        self.protocol_endpoint_kind = QComboBox()
        self.protocol_endpoint_kind.addItem("Dose received by a volume (Dxx)", "d_percent")
        self.protocol_endpoint_kind.addItem("Volume receiving a percentage of prescription (Vxx%Rx)", "coverage_relative_rx")
        self.protocol_endpoint_kind.addItem("Volume receiving an absolute dose (VxxGy)", "coverage_absolute_gy")
        self.protocol_endpoint_value = QLineEdit(); self.protocol_endpoint_value.setValidator(QDoubleValidator(0.000001, 100000.0, 6))
        self.protocol_endpoint_value.setPlaceholderText("95")
        add_endpoint = QPushButton("Add endpoint"); add_endpoint.clicked.connect(self._add_protocol_endpoint)
        remove_endpoint = QPushButton("Remove selected"); remove_endpoint.clicked.connect(self._remove_protocol_endpoint)
        self.prefill_endpoint_button = QPushButton("Auto-fill from Eclipse reference")
        self.prefill_endpoint_button.clicked.connect(self._prefill_protocol_endpoints)
        endpoint_selectors.addWidget(self.protocol_endpoint_role)
        endpoint_selectors.addWidget(self.protocol_endpoint_kind, 1)
        endpoint_selectors.addWidget(self.protocol_endpoint_value)
        endpoint_layout.addLayout(endpoint_selectors)
        endpoint_actions = QHBoxLayout()
        endpoint_actions.addWidget(add_endpoint)
        endpoint_actions.addWidget(remove_endpoint)
        endpoint_actions.addWidget(self.prefill_endpoint_button)
        endpoint_actions.addStretch()
        endpoint_layout.addLayout(endpoint_actions)
        self.protocol_endpoint_table = _table(["Structure role", "Endpoint", "Configured value", "Source"])
        self.protocol_endpoint_table.setMaximumHeight(180)
        endpoint_layout.addWidget(self.protocol_endpoint_table)
        self._protocol_endpoint_entries: list[dict[str, Any]] = []
        form.addRow("Treatment approach", self.treatment_approach)
        form.addRow("Detailed delivery mode", self.mode)
        form.addRow("Dose context", self.dose_context)
        form.addRow("Peripheral prescription Rx_L (Gy)", self.rx_l)
        form.addRow("Rx_L source", self.rx_l_source)
        form.addRow("High-dose prescription Rx_H (Gy)", self.rx_h)
        form.addRow("Rx_H source", self.rx_h_source)
        form.addRow("Fractions", self.fractions)
        form.addRow("Protocol identifier", self.protocol_id)
        form.addRow("Protocol endpoints", endpoint_widget)
        layout.addLayout(form)

        component_card, component_layout = self._card(
            "Treatment components",
            "Enter only documented component history. Dates and preceding gaps are retained as provenance; no gap correction or dose warping is performed.",
        )
        component_controls = QGridLayout()
        self.component_id = QLineEdit(); self.component_id.setPlaceholderText("Component ID")
        self.component_type = QComboBox(); self.component_type.addItems(["LRT", "CERT", "OTHER"])
        self.component_prescription = QLineEdit(); self.component_prescription.setPlaceholderText("Prescription Gy")
        self.component_fractions = QLineEdit(); self.component_fractions.setPlaceholderText("Fractions")
        self.component_rx_low = QLineEdit(); self.component_rx_low.setPlaceholderText("Component Rx_L Gy")
        self.component_rx_high = QLineEdit(); self.component_rx_high.setPlaceholderText("Component Rx_H Gy")
        self.component_start = QLineEdit(); self.component_start.setPlaceholderText("Start date/time optional")
        self.component_end = QLineEdit(); self.component_end.setPlaceholderText("End date/time optional")
        self.component_gap = QLineEdit(); self.component_gap.setPlaceholderText("Preceding gap days")
        self.component_prescription_source = QLineEdit(); self.component_prescription_source.setPlaceholderText("Prescription source")
        self.component_dose_uid = QLineEdit(); self.component_dose_uid.setPlaceholderText("RTDOSE SOP Instance UID optional")
        self.component_plan_uid = QLineEdit(); self.component_plan_uid.setPlaceholderText("RTPLAN SOP Instance UID optional")
        self.component_geometry = QLineEdit(); self.component_geometry.setPlaceholderText("Validated geometry ID/hash optional")
        for column, (label, widget) in enumerate((
            ("ID", self.component_id), ("Type", self.component_type), ("Prescription", self.component_prescription),
            ("Fractions", self.component_fractions), ("Rx_L", self.component_rx_low), ("Rx_H", self.component_rx_high),
        )):
            component_controls.addWidget(QLabel(label), 0, column); component_controls.addWidget(widget, 1, column)
        for column, (label, widget) in enumerate((
            ("Start", self.component_start), ("End", self.component_end), ("Gap days", self.component_gap),
            ("Prescription source", self.component_prescription_source),
        )):
            component_controls.addWidget(QLabel(label), 2, column); component_controls.addWidget(widget, 3, column)
        add_component = QPushButton("Add / replace component"); add_component.clicked.connect(self._add_treatment_component)
        remove_component = QPushButton("Remove selected component"); remove_component.clicked.connect(self._remove_treatment_component)
        component_controls.addWidget(add_component, 3, 4); component_controls.addWidget(remove_component, 3, 5)
        for column, (label, widget) in enumerate((
            ("Dose source UID", self.component_dose_uid), ("Plan UID", self.component_plan_uid),
            ("Geometry identity", self.component_geometry),
        )):
            component_controls.addWidget(QLabel(label), 4, column * 2)
            component_controls.addWidget(widget, 5, column * 2, 1, 2)
        component_layout.addLayout(component_controls)
        self.treatment_component_table = _table([
            "Component", "Type", "Prescription Gy", "Fractions", "Dose/fraction Gy",
            "Rx_L", "Rx_H", "Start", "End", "Preceding gap days", "Source", "Dose UID", "Geometry",
        ])
        self.treatment_component_table.setMaximumHeight(190)
        component_layout.addWidget(self.treatment_component_table)
        analysis_row = QHBoxLayout(); self.analysis_component = QComboBox()
        analysis_row.addWidget(QLabel("Analysis component")); analysis_row.addWidget(self.analysis_component, 1)
        component_layout.addLayout(analysis_row)
        self._treatment_component_entries: list[dict[str, Any]] = []
        layout.addWidget(component_card)

        # Keep variable-length RTPLAN evidence outside QFormLayout.  A table in
        # one form row is compressed by the neighbouring label column on macOS,
        # making the candidates appear clipped or hidden below endpoint rows.
        candidate_card, candidate_layout = self._card(
            "DICOM-derived candidates",
            "Read-only RTPLAN/RTDOSE evidence. ASCEND prefills only a unique unambiguous value; multiple candidates require explicit selection above.",
        )
        self.dicom_prefill_summary = QLabel("No selected RTPLAN/RTDOSE configuration evidence.")
        self.dicom_prefill_summary.setObjectName("sectionDescription")
        self.dicom_prefill_summary.setWordWrap(True)
        candidate_layout.addWidget(self.dicom_prefill_summary)
        self.dicom_candidate_tabs = QTabWidget()
        self.dicom_fraction_candidates = _table(["Fraction group", "Fractions", "Referenced beams", "DICOM source"])
        self.dicom_prescription_candidates = _table([
            "Dose reference", "Dose (Gy)", "Label", "Referenced ROI", "Reference type", "DICOM source",
        ])
        self.dicom_fraction_candidates.setMinimumHeight(120)
        self.dicom_prescription_candidates.setMinimumHeight(120)
        self.dicom_candidate_tabs.addTab(self.dicom_fraction_candidates, "Fractionation")
        self.dicom_candidate_tabs.addTab(self.dicom_prescription_candidates, "Prescriptions")
        candidate_layout.addWidget(self.dicom_candidate_tabs)
        self.dicom_candidate_warnings = WarningBanner()
        candidate_layout.addWidget(self.dicom_candidate_warnings)
        layout.addWidget(candidate_card)

        checks = QGridLayout()
        self.confirm_prescriptions = QCheckBox("Prescriptions confirmed")
        self.confirm_roles = QCheckBox("Roles confirmed")
        self.confirm_dose = QCheckBox("Dose object confirmed")
        self.confirm_valley = QCheckBox("Valley definition confirmed")
        self.confirm_equal = QCheckBox("Equal prescriptions intentional")
        for index, widget in enumerate((
            self.confirm_prescriptions, self.confirm_roles, self.confirm_dose,
            self.confirm_valley, self.confirm_equal,
        )):
            checks.addWidget(widget, index // 3, index % 3)
        layout.addLayout(checks)
        save = QPushButton("Save configuration")
        save.setObjectName("primary")
        save.clicked.connect(self._save_configuration)
        layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addStretch()

    def _build_mapping_page(self) -> None:
        _, layout = self._new_page("Structure-role mapping", "Bind RTSTRUCT ROI identities to vendor-independent ASCEND roles. Names are displayed for review; the stored ROI number and RTSTRUCT UID remain authoritative.")
        mapping_card, mapping_layout = self._card(
            "Role assignments",
            "Required roles must resolve to one unambiguous ROI. Individual vertices may be comma-separated.",
        )
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        self.role_widgets: dict[str, QComboBox | QLineEdit] = {}
        labels = {
            "GTV": "GTV",
            "T_L": "Peripheral target T_L",
            "VTV_H": "Aggregate high-dose VTV_H",
            "VTV_L": "Planned valley VTV_L",
            "VTV_H_individual": "Individual high-dose vertices",
        }
        for role, label in labels.items():
            widget: QComboBox | QLineEdit
            if role == "VTV_H_individual":
                widget = QLineEdit()
            else:
                widget = QComboBox(); widget.setEditable(True)
            self.role_widgets[role] = widget
            form.addRow(label, widget)
        self.validation_structures = QLineEdit()
        self.validation_structures.setPlaceholderText("Optional comma-separated RTSTRUCT names for Layer 1 validation")
        form.addRow("Additional validation structures", self.validation_structures)
        mapping_layout.addLayout(form)
        oar_heading = QLabel("Optional OAR and internal-target geometry")
        oar_heading.setObjectName("sectionTitle")
        mapping_layout.addWidget(oar_heading)
        oar_detail = QLabel(
            "Select structures for descriptive geometry only. OARs can be compared with vertices; all_vertices/all_valleys may instead be labelled internal target structures and are never treated as OAR compliance structures."
        )
        oar_detail.setObjectName("sectionDescription")
        oar_detail.setWordWrap(True)
        mapping_layout.addWidget(oar_detail)
        oar_controls = QHBoxLayout()
        self.oar_roi_selector = QComboBox()
        self.oar_roi_selector.addItem("Open a case to load RTSTRUCT ROIs…", None)
        self.oar_roi_selector.setMinimumWidth(280)
        self.oar_roi_selector.currentIndexChanged.connect(self._infer_geometry_classification)
        self.oar_classification_selector = QComboBox()
        classification_labels = {
            "containing_organ": "Containing organ",
            "target_excluded_oar": "Target-excluded OAR",
            "separate_critical_oar": "Separate critical OAR",
            "internal_target_structure": "Internal target structure (for example all_vertices/all_valleys)",
        }
        for classification in OAR_CLASSIFICATIONS:
            self.oar_classification_selector.addItem(classification_labels[classification], classification)
        add_oar = QPushButton("Add / update OAR")
        add_oar.clicked.connect(self._add_or_update_oar)
        remove_oar = QPushButton("Remove selected OAR")
        remove_oar.clicked.connect(self._remove_selected_oar)
        prefill_oars = QPushButton("Auto-fill OAR candidates")
        prefill_oars.clicked.connect(self._prefill_oar_geometry)
        oar_controls.addWidget(QLabel("RTSTRUCT ROI"))
        oar_controls.addWidget(self.oar_roi_selector, 2)
        oar_controls.addWidget(QLabel("Classification"))
        oar_controls.addWidget(self.oar_classification_selector, 1)
        oar_controls.addWidget(add_oar)
        oar_controls.addWidget(remove_oar)
        oar_controls.addWidget(prefill_oars)
        mapping_layout.addLayout(oar_controls)
        self.oar_table = _table(["OAR", "ROI number", "Classification", "Identity binding"])
        self.oar_table.setMaximumHeight(190)
        self.oar_table.cellClicked.connect(self._select_oar_table_row)
        mapping_layout.addWidget(self.oar_table)
        self._oar_entries: list[dict[str, Any]] = []
        save = QPushButton("Save mappings")
        save.setObjectName("primary")
        save.clicked.connect(self._save_configuration)
        mapping_layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addWidget(mapping_card)
        inventory_card, inventory_layout = self._card(
            "Resolved bindings",
            "The table reflects stored identity bindings and whether each required role is ready for Layer 1.",
        )
        self.mapping_table = _table(["ASCEND role", "Requirement", "RTSTRUCT ROI", "ROI number", "Binding state"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        inventory_layout.addWidget(self.mapping_table)
        layout.addWidget(inventory_card, 1)

    def _build_layer1_page(self) -> None:
        _, layout = self._new_page("Layer 1 validation", "Data-quality gateway and immutable canonical native archive.")
        row = QHBoxLayout()
        run = QPushButton("Validate case")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer1)
        self.layer1_status_pill = StatusPill("NOT RUN")
        self.layer1_card = QLabel("Downstream eligibility is closed until validation completes.")
        self.layer1_card.setObjectName("sectionDescription")
        row.addWidget(run)
        row.addWidget(self.layer1_status_pill)
        row.addWidget(self.layer1_card, 1)
        layout.addLayout(row)
        self.layer1_banner = WarningBanner("No Layer 1 warnings recorded.")
        layout.addWidget(self.layer1_banner)
        self.layer1_tabs = QTabWidget()
        self.layer1_findings = _table(["Severity", "Check", "Detail", "Downstream effect"])
        self.layer1_eclipse_audit = _table([
            "Eclipse structure", "ASCEND role", "Validated structure", "Metric",
            "Eclipse", "ASCEND", "Difference", "Unit", "Status",
        ])
        self.layer1_eclipse_import = _text_view()
        self.layer1_tabs.addTab(self.layer1_findings, "Validation findings")
        self.layer1_tabs.addTab(self.layer1_eclipse_audit, "Eclipse DVH audit")
        self.layer1_tabs.addTab(self.layer1_eclipse_import, "Import provenance")
        layout.addWidget(self.layer1_tabs, 1)

    def _build_layer21_page(self) -> None:
        _, layout = self._new_page("Layer 2.1 — LRT physical metrics", "Warnings and applicability precede the six locked results. Cards are presentation adapters over stored Layer 2.1 records.")
        selection_card, selection_layout = self._card(
            "Select optional supporting calculations before running Layer 2.1",
            "The locked six primary metrics always run. Unselected per-vertex QA, protocol endpoints, and OAR geometry are skipped rather than calculated and hidden.",
        )
        selection_row = QHBoxLayout()
        self._current_supporting_outputs: dict[str, Any] = {}
        self.supporting_outputs_enabled = QCheckBox("Calculate supporting outputs")
        self.supporting_outputs_enabled.setChecked(True)
        selection_row.addWidget(self.supporting_outputs_enabled)
        supporting_labels = {
            "coverage": "Coverage and volume context",
            "peak_valley": "Peak, valley and ratio context",
            "per_vertex": "Per-vertex QA",
            "protocol_native": "Protocol endpoints",
            "oar_geometry": "OAR / target geometry",
            "integrity": "Integrity and provenance",
        }
        self.supporting_output_checks: dict[str, QCheckBox] = {}
        for category in DEFAULT_SUPPORTING_OUTPUT_CATEGORIES:
            checkbox = QCheckBox(supporting_labels[category]); checkbox.setChecked(True)
            checkbox.toggled.connect(lambda _checked: self._refresh_layer21(self.controller.case) if self.controller.case else None)
            self.supporting_output_checks[category] = checkbox
            selection_row.addWidget(checkbox)
        selection_row.addStretch()
        selection_layout.addLayout(selection_row)
        self.supporting_outputs_enabled.toggled.connect(self._toggle_supporting_output_controls)
        layout.addWidget(selection_card)
        row = QHBoxLayout()
        run = QPushButton("Run Layer 2.1")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer21)
        physical = QPushButton("Run physical analysis (2.1 + 2.2)")
        physical.clicked.connect(self._run_physical)
        self.layer21_status_pill = StatusPill("NOT RUN")
        self.layer21_interpretation_pill = StatusPill("NOT RUN")
        self.layer21_card = QLabel("No Layer 2.1 result")
        self.layer21_card.setObjectName("sectionDescription")
        row.addWidget(run); row.addWidget(physical); row.addWidget(self.layer21_status_pill)
        row.addWidget(self.layer21_interpretation_pill); row.addWidget(self.layer21_card, 1)
        layout.addLayout(row)
        self.layer21_warnings = WarningBanner("No Layer 2.1 warnings recorded.")
        layout.addWidget(self.layer21_warnings)
        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        self.metric_cards: dict[str, MetricCard] = {}
        metric_order = list(METRIC_LABELS)
        for index, metric_id in enumerate(metric_order):
            card = MetricCard(metric_id)
            self.metric_cards[metric_id] = card
            cards.addWidget(card, index // 3, index % 3)
        layout.addLayout(cards)
        self.metric_table = _table(["Metric", "Value", "Units", "Applicability", "Warnings"])
        self.metric_table.setMaximumHeight(220)
        layout.addWidget(self.metric_table)
        self.layer21_tabs = WorkstationToolBox()
        self.layer21_tabs.setMinimumHeight(390)
        supporting_page = QWidget(); supporting_layout = QVBoxLayout(supporting_page)
        supporting_layout.setContentsMargins(8, 8, 8, 8)
        supporting_header = QHBoxLayout()
        supporting_description = QLabel("Stored supporting-output fields; no GUI recalculation.")
        supporting_description.setObjectName("sectionDescription")
        self.export_supporting_json_button = QPushButton("Export supporting outputs JSON")
        self.export_supporting_json_button.clicked.connect(self._export_supporting_outputs_json)
        self.export_supporting_json_button.setEnabled(False)
        supporting_header.addWidget(supporting_description, 1)
        supporting_header.addWidget(self.export_supporting_json_button)
        supporting_layout.addLayout(supporting_header)
        self.layer21_support = _table(["Section", "Item", "Value", "Units", "Recorded status / warnings"])
        self.layer21_support.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.layer21_support.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.layer21_support.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.layer21_support.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.layer21_support.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        supporting_layout.addWidget(self.layer21_support)
        vertex_page = QWidget(); vertex_layout = QVBoxLayout(vertex_page)
        vertex_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_vertex_summary = QLabel("No per-vertex QA records")
        self.layer21_vertex_summary.setObjectName("sectionDescription")
        self.layer21_vertex_table = _table(["Vertex", "V95 RxH (%)", "Applicability", "Dmean (Gy)", "D95 (Gy)", "Dmax (Gy)", "Volume (cc)"])
        self.layer21_vertex = _text_view(); self.layer21_vertex.setMaximumHeight(150)
        vertex_layout.addWidget(self.layer21_vertex_summary)
        vertex_layout.addWidget(self.layer21_vertex_table)
        vertex_layout.addWidget(self.layer21_vertex)
        oar_page = QWidget(); oar_layout = QVBoxLayout(oar_page); oar_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_oar_status = QLabel("No optional OAR or internal-target geometry result.")
        self.layer21_oar_status.setObjectName("sectionDescription")
        self.layer21_oar_table = _table([
            "Structure", "Classification", "Volume (cc)", "VTVH min separation (mm)", "Relationship",
            "Overlap (cc)", "Overlap of structure (%)", "Nearest vertex", "Vertex min separation (mm)", "Audit",
        ])
        oar_layout.addWidget(self.layer21_oar_status)
        oar_layout.addWidget(self.layer21_oar_table)
        self.layer21_oar_vertex_table = _table([
            "Structure", "Vertex", "Min separation (mm)", "Overlap (cc)", "Relationship", "Zero-distance reason",
        ])
        oar_layout.addWidget(self.layer21_oar_vertex_table)
        provenance_page = QWidget(); provenance_layout = QVBoxLayout(provenance_page); provenance_layout.setContentsMargins(8, 8, 8, 8)
        self.layer21_provenance = _text_view(); provenance_layout.addWidget(self.layer21_provenance)
        self.layer21_tabs.addItem(supporting_page, "Supporting outputs")
        self.layer21_tabs.addItem(vertex_page, "Per-vertex QA")
        self.layer21_tabs.addItem(oar_page, "OAR geometry")
        self.layer21_tabs.addItem(provenance_page, "Provenance")
        layout.addWidget(self.layer21_tabs, 1)

    def _toggle_supporting_output_controls(self, enabled: bool) -> None:
        for checkbox in getattr(self, "supporting_output_checks", {}).values():
            checkbox.setEnabled(enabled)
        if hasattr(self, "export_supporting_json_button"):
            self.export_supporting_json_button.setEnabled(enabled and bool(self._current_supporting_outputs))
        if self.controller.case and not self._loading_configuration:
            self._refresh_layer21(self.controller.case)

    def _build_layer22_page(self) -> None:
        _, layout = self._new_page("Layer 2.2 — Spatial PVDR", "Profile-orientation-independent nearest-neighbour graph using validated native geometry.")
        row = QHBoxLayout()
        run = QPushButton("Run Layer 2.2")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer22)
        build_viewer = QPushButton("Build / refresh 3D viewer")
        build_viewer.clicked.connect(self._build_layer22_visualization)
        self.layer22_status_pill = StatusPill("NOT RUN")
        self.layer22_interpretation_pill = StatusPill("NOT RUN")
        self.layer22_card = QLabel("No Layer 2.2 result")
        self.layer22_card.setObjectName("sectionDescription")
        row.addWidget(run); row.addWidget(build_viewer); row.addWidget(self.layer22_status_pill)
        row.addWidget(self.layer22_interpretation_pill); row.addWidget(self.layer22_card, 1)
        layout.addLayout(row)
        self.layer22_warnings = WarningBanner("No Layer 2.2 warnings recorded.")
        layout.addWidget(self.layer22_warnings)
        self.layer22_display_tabs = QTabWidget()
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        controls_card, controls_layout = self._card("Graph controls", "Projection and visibility affect presentation only; stored node and edge records remain unchanged.")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Projection"))
        self.graph_projection = QComboBox()
        for label, value in (("Automatic", "auto"), ("Axial (X–Y)", "axial"), ("Sagittal (Y–Z)", "sagittal"), ("Coronal (X–Z)", "coronal")):
            self.graph_projection.addItem(label, value)
        self.graph_projection.currentIndexChanged.connect(
            lambda _index: self.graph_canvas.set_projection(str(self.graph_projection.currentData()))
        )
        self.graph_edge_labels = QCheckBox("Edge iPVDR labels")
        self.graph_edge_labels.setChecked(True)
        self.graph_edge_labels.toggled.connect(
            lambda visible: self.graph_canvas.set_edge_labels_visible(visible)
        )
        self.graph_invalid_edges = QCheckBox("Invalid edges")
        self.graph_invalid_edges.setChecked(True)
        self.graph_invalid_edges.toggled.connect(
            lambda visible: self.graph_canvas.set_invalid_edges_visible(visible)
        )
        self.graph_result_summary = QLabel("No graph result")
        self.graph_result_summary.setObjectName("sectionDescription")
        controls.addWidget(self.graph_projection)
        controls.addWidget(self.graph_edge_labels)
        controls.addWidget(self.graph_invalid_edges)
        for label, operation in (("−", lambda: self.graph_canvas.zoom_by(1/1.2)), ("+", lambda: self.graph_canvas.zoom_by(1.2)),
                                 ("↺", lambda: self.graph_canvas.rotate_by(-15)), ("↻", lambda: self.graph_canvas.rotate_by(15)),
                                 ("Fit", lambda: self.graph_canvas.reset_view())):
            button = QPushButton(label); button.clicked.connect(operation); controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.graph_result_summary)
        controls_layout.addLayout(controls)
        overview_layout.addWidget(controls_card)
        split = QSplitter(Qt.Horizontal)
        self.graph_canvas = GraphCanvas()
        split.addWidget(self.graph_canvas)
        tabs = QTabWidget()
        self.graph_summary = _table(["Summary item", "Value", "Meaning"])
        self.graph_nodes = _table(["Node", "X", "Y", "Z", "D50 (Gy)"])
        self.graph_edges = _table(["Edge", "Nodes", "Length (mm)", "Valley D50", "iPVDR", "Status"])
        self.graph_provenance = _text_view()
        tabs.addTab(self.graph_summary, "Summary")
        tabs.addTab(self.graph_nodes, "Nodes")
        tabs.addTab(self.graph_edges, "Edges")
        tabs.addTab(self.graph_provenance, "Provenance")
        split.addWidget(tabs)
        split.setSizes([620, 560])
        overview_layout.addWidget(split)
        self.layer22_display_tabs.addTab(overview, "Graph overview")
        viewer_page = QWidget()
        self.layer22_viewer_layout = QVBoxLayout(viewer_page)
        self.layer22_viewer_layout.setContentsMargins(0, 0, 0, 0)
        self.layer22_viewer_status = QLabel("Run Layer 2.2, then build the 3D viewer.")
        self.layer22_viewer_status.setWordWrap(True)
        self.layer22_viewer_layout.addWidget(self.layer22_viewer_status)
        self.layer22_viewer_layout.addStretch()
        self.layer22_display_tabs.addTab(viewer_page, "3D masks / dose views")
        layout.addWidget(self.layer22_display_tabs, 1)

    def _build_placeholder_page(self, title: str, detail: str) -> None:
        _, layout = self._new_page(title)
        layout.addWidget(StatePanel("NOT IMPLEMENTED", "Module interface only", detail), 1)
        layout.addStretch()

    def _build_layer32_page(self) -> None:
        """Build structured controls and read-only views for stored Layer 3.2 evidence."""
        _, layout = self._new_page(
            "Layer 3.2 — Non-local biological reinterpretation",
            "Research-only downstream reinterpretation using current Layer 1, Layer 2.2, and Layer 3.1 evidence.",
        )
        self.layer32_research_banner = WarningBanner(
            "RESEARCH MODEL · NOT CLINICALLY CALIBRATED · NOT A TOXICITY PREDICTION"
        )
        layout.addWidget(self.layer32_research_banner)
        enable_row = QHBoxLayout()
        self.layer32_enabled = QCheckBox("Enable Layer 3.2 non-local research model")
        self.layer32_enabled.setObjectName("layer32EnableToggle")
        self.layer32_enabled.setToolTip(
            "Layer 3.2 is excluded from calculation, result presentation, and export until explicitly enabled."
        )
        self.layer32_enabled.toggled.connect(self._layer32_enabled_changed)
        enable_detail = QLabel("Optional downstream research analysis. Disabled by default.")
        enable_detail.setObjectName("sectionDescription")
        enable_row.addWidget(self.layer32_enabled)
        enable_row.addWidget(enable_detail, 1)
        layout.addLayout(enable_row)
        status = QHBoxLayout()
        self.layer32_run_button = QPushButton("Run Layer 3.2")
        self.layer32_run_button.setObjectName("primary")
        self.layer32_run_button.clicked.connect(self._run_layer32)
        self.layer32_viewer_button = QPushButton("Build / refresh 3D biological field viewer")
        self.layer32_viewer_button.clicked.connect(self._build_layer32_visualization)
        self.layer32_status_pill = StatusPill("NOT RUN")
        self.layer32_interpretation_pill = StatusPill("NOT RUN")
        self.layer32_status_text = QLabel("Requires current Layer 1, Layer 2.2, and Layer 3.1 results.")
        self.layer32_status_text.setObjectName("sectionDescription")
        status.addWidget(self.layer32_run_button); status.addWidget(self.layer32_viewer_button); status.addWidget(self.layer32_status_pill)
        status.addWidget(self.layer32_interpretation_pill); status.addWidget(self.layer32_status_text, 1)
        layout.addLayout(status)
        self.layer32_warnings = WarningBanner("No Layer 3.2 warnings recorded.")
        layout.addWidget(self.layer32_warnings)

        parameters_card, parameters_layout = self._card(
            "Model parameters",
            "Reference parameters are structured controls. Vascular geometry and vascular uptake are absent from this layer; the PDE has no sink term.",
        )
        exposure_definition = QLabel(
            "Cumulative non-local mediator exposure, H: Time-integrated weighted exposure to the modelled "
            "ROS-like and cytokine-like fields. Higher values indicate stronger accumulated modelled signalling. "
            "Dimensionless; not physical dose, measured concentration, toxicity probability or clinical risk."
        )
        exposure_definition.setObjectName("sectionDescription"); exposure_definition.setWordWrap(True)
        parameters_layout.addWidget(exposure_definition)
        parameter_row = QHBoxLayout()
        self.layer32_preset = QComboBox(); self.layer32_preset.addItem("SFRT-MODEL1 reference · no vascular uptake", "sfrt_model1_no_uptake")
        self.layer32_scaling = QLineEdit(); self.layer32_scaling.setPlaceholderText("Non-local scaling")
        self.layer32_steps = QLineEdit(); self.layer32_steps.setPlaceholderText("PDE steps")
        self.layer32_dt = QLineEdit(); self.layer32_dt.setPlaceholderText("PDE dt")
        self.layer32_grid_spacing = QLineEdit(); self.layer32_grid_spacing.setPlaceholderText("Model grid mm")
        self.layer32_margin = QLineEdit(); self.layer32_margin.setPlaceholderText("GTV margin mm")
        for label, widget in (
            ("Preset", self.layer32_preset), ("Scaling", self.layer32_scaling), ("Steps", self.layer32_steps),
            ("dt", self.layer32_dt), ("Grid", self.layer32_grid_spacing), ("Domain", self.layer32_margin),
        ):
            parameter_row.addWidget(QLabel(label)); parameter_row.addWidget(widget)
        parameters_layout.addLayout(parameter_row)
        self.layer32_parameter_table = _table(["Parameter", "Value", "Units", "Source"])
        self.layer32_parameter_table.setMaximumHeight(210)
        parameters_layout.addWidget(self.layer32_parameter_table)
        self.layer32_configuration_summary = _table(["Current configuration", "Value", "State / units"])
        self.layer32_configuration_summary.setMaximumHeight(235)
        parameters_layout.addWidget(self.layer32_configuration_summary)
        self.layer32_scenario_table = _table(["Comparison scenario", "Status", "Meaning"])
        self.layer32_scenario_table.setMaximumHeight(160)
        parameters_layout.addWidget(self.layer32_scenario_table)
        layout.addWidget(parameters_card)

        summary_card, summary_layout = self._card(
            "Biological graph reinterpretation",
            "iPVDR shift is signed. A negative shift indicates biological contrast compression; ASCEND does not assume an uplift.",
        )
        self.layer32_graph_summary = _table(["Metric", "Value", "Meaning"])
        self.layer32_edge_table = _table([
            "Edge", "Nodes", "Physical iPVDR", "Baseline LQ iPVDR", "Biological iPVDR",
            "Biological shift", "Non-local-only shift", "Valley effect shift",
        ])
        summary_layout.addWidget(self.layer32_graph_summary); summary_layout.addWidget(self.layer32_edge_table)
        layout.addWidget(summary_card)

        context_tabs = QTabWidget()
        gtv_page = QWidget(); gtv_layout = QVBoxLayout(gtv_page)
        self.layer32_gtv_table = _table(["Field", "Mean", "D95", "D50", "D2", "Units"]); gtv_layout.addWidget(self.layer32_gtv_table)
        spill_page = QWidget(); spill_layout = QVBoxLayout(spill_page)
        self.layer32_shell_table = _table(["Shell (mm)", "Voxels", "Physical mean", "Biological mean", "Additional effect mean", "Survival mean"]); spill_layout.addWidget(self.layer32_shell_table)
        oar_page = QWidget(); oar_layout = QVBoxLayout(oar_page)
        self.layer32_oar_table = _table(["OAR", "Classification", "Nearest vertex", "Distance (mm)", "Physical mean", "Biological mean", "Biological D2", "Additional effect mean", "Compliance"]); oar_layout.addWidget(self.layer32_oar_table)
        assay_page = QWidget(); assay_layout = QVBoxLayout(assay_page)
        self.layer32_assay_table = _table(["Observable", "Mean", "Maximum", "Units", "Scope"]); assay_layout.addWidget(self.layer32_assay_table)
        regional_page = QWidget(); regional_layout = QVBoxLayout(regional_page)
        regional_title = QLabel("Modelled regional exposure and consequence"); regional_title.setObjectName("sectionTitle")
        regional_layout.addWidget(regional_title)
        self.layer32_regional_table = _table([
            "Structure", "Mean H", "P95 H", "Mean additional reduction", "Maximum reduction",
            "Volume ≥5%", "Final survival change",
        ])
        regional_layout.addWidget(self.layer32_regional_table)
        context_tabs.addTab(gtv_page, "GTV context"); context_tabs.addTab(spill_page, "Peri-GTV spill")
        context_tabs.addTab(oar_page, "Adjacent OAR spill"); context_tabs.addTab(assay_page, "Model observables")
        context_tabs.addTab(regional_page, "Regional exposure and consequence")
        layout.addWidget(context_tabs)

        viewer_card, viewer_layout = self._card(
            "3D biological fields, anatomical CAD surfaces, and graph profiles",
            "The viewer renders stored scalar isosurfaces in DICOM LPS coordinates. Edge profiles are visualisation-only; Layer 3.2 iPVDR still uses the unchanged Layer 2.2 node and 3 mm midpoint-sphere definitions.",
        )
        self.layer32_viewer_status = QLabel("Run Layer 3.2, then build the hash-verified 3D/2D stored-field viewer.")
        self.layer32_viewer_status.setObjectName("sectionDescription"); self.layer32_viewer_status.setWordWrap(True)
        self.layer32_viewer_layout = viewer_layout; viewer_layout.addWidget(self.layer32_viewer_status)
        layout.addWidget(viewer_card, 1)

        provenance_card, provenance_layout = self._card("Model provenance", "Source commit, hashes, dependency run IDs, and no-uptake assertions.")
        self.layer32_provenance = _text_view(); self.layer32_provenance.setMaximumHeight(240)
        provenance_layout.addWidget(self.layer32_provenance); layout.addWidget(provenance_card)
        self.layer32_parameter_controls = [
            self.layer32_preset, self.layer32_scaling, self.layer32_steps,
            self.layer32_dt, self.layer32_grid_spacing, self.layer32_margin,
        ]
        self._update_layer32_enabled_controls(False)

    def _update_layer32_enabled_controls(self, enabled: bool) -> None:
        """Apply the optional Layer 3.2 gate to controls without running science."""
        self.layer32_run_button.setEnabled(enabled)
        has_current_result = bool(
            self.controller.case
            and self.controller.case.layer3_2.calculation_status in {"completed", "completed_with_warnings"}
        )
        self.layer32_viewer_button.setEnabled(enabled and has_current_result)
        for widget in self.layer32_parameter_controls:
            widget.setEnabled(enabled)
        if self.layer32_viewer is not None:
            self.layer32_viewer.setEnabled(enabled and has_current_result)

    def _layer32_enabled_changed(self, enabled: bool) -> None:
        """Persist the Layer 3.2 inclusion decision and invalidate only its evidence."""
        self._update_layer32_enabled_controls(enabled)
        case = self.controller.case
        if case is None or self._loading_configuration:
            return
        configuration = CaseConfiguration.from_dict(case.configuration.to_dict())
        configuration.layer32_enabled = enabled
        self.controller.configure(configuration)
        self.activity.setText("L3.2 ENABLED" if enabled else "L3.2 DISABLED")
        self.refresh()

    def _build_layer31_page(self) -> None:
        _, layout = self._new_page(
            "Layer 3.1 — Spatial Radiobiological Evaluation",
            "One linked anatomical workspace for physical dose, spatial LQ BED/EQD2, Guerrero–Li tumour survival/effect, regional decomposition, and gated therapeutic ratio.",
        )
        scope = WarningBanner()
        scope.set_messages([
            "RESEARCH MODEL — NOT CLINICALLY VALIDATED. Outputs are model-derived comparative quantities, not TCP, NTCP, toxicity, or treatment recommendations."
        ], blocked=False)
        layout.addWidget(scope)
        actions = QHBoxLayout()
        run = QPushButton("Run complete Layer 3.1")
        run.setObjectName("primary")
        run.clicked.connect(self._run_layer31)
        build = QPushButton("Open unified spatial viewer")
        build.clicked.connect(self._build_layer31_visualization)
        export = QPushButton("Export Layer 3.1")
        export.clicked.connect(self._export_layer31)
        actions.addWidget(run); actions.addWidget(build); actions.addWidget(export); actions.addStretch()
        layout.addLayout(actions)
        self.layer31_status_pill = StatusPill("NOT RUN")
        self.layer31_interpretation_pill = StatusPill("NOT INTERPRETABLE")
        self.layer31_status_text = QLabel("Configure tissue and model parameters, then run the gated workflow.")
        self.layer31_status_text.setWordWrap(True)
        status = QHBoxLayout()
        status.addWidget(self.layer31_status_pill)
        status.addWidget(self.layer31_interpretation_pill)
        status.addWidget(self.layer31_status_text, 1)
        layout.addLayout(status)

        self.layer31_tabs = QTabWidget()
        layout.addWidget(self.layer31_tabs, 1)

        overview = QWidget(); overview_layout = QVBoxLayout(overview)
        workflow_card, workflow_layout = self._card(
            "Layer 3.1 guided order · steps 1–17",
            "Complete the workflow from left to right. Results are deliberately presented as map → whole-tumour result → regional explanation.",
        )
        self.layer31_workflow_order = QLabel(
            "1 Prepare case  →  2 Review treatment history  →  3 Select dose sources  →  "
            "4 Assign tissue parameters  →  5 Configure LQ warning  →  6 Configure tumour model  →  "
            "7 Configure normal-cell model when required  →  8 Define TR comparator when required  →  "
            "9 Save and run  →  10 Select map  →  11 Navigate linked planes  →  12 Control anatomy  →  "
            "13 Inspect/export CAD  →  14 Read tumour SF/EUD  →  15 Explain regional contribution  →  "
            "16 Recalculate scenarios/assess TR  →  17 Audit provenance/export"
        )
        self.layer31_workflow_order.setObjectName("sectionDescription")
        self.layer31_workflow_order.setWordWrap(True)
        workflow_layout.addWidget(self.layer31_workflow_order)
        overview_layout.addWidget(workflow_card)
        gate_card, gate_layout = self._card("Step 1 — Prepare case and pass analysis gates", "A blocked prerequisite cannot produce normal-looking biological outputs. Complete DICOM selection, treatment configuration, structure mapping, and Layer 1 validation before biological analysis.")
        self.layer31_gate_table = _table(["Gate / branch", "State", "Reason / evidence"])
        gate_layout.addWidget(self.layer31_gate_table); overview_layout.addWidget(gate_card)
        history_card, history_layout = self._card("Step 2 — Review treatment and dose history", "Physical components are grouped into biologically distinct fraction events before nonlinear transformation. Confirm that every contributing dose shares one validated physical geometry.")
        self.layer31_history_table = _table(["Order", "Event", "Components", "Fraction index", "Geometry", "Delivery time", "Source dose / plan"])
        history_layout.addWidget(self.layer31_history_table); overview_layout.addWidget(history_card)

        config_card, config_layout = self._card("Steps 3–9 — Biological configuration and run", "All parameters are explicit, identity-bound, and stored with provenance. No JSON entry is required.")
        source_title = QLabel("Step 3 — Select treatment-source representation")
        source_title.setObjectName("sectionTitle"); config_layout.addWidget(source_title)
        source_note = QLabel("Use repeated identical fractions for a validated component total, or explicit per-fraction validated doses when fraction fields differ.")
        source_note.setObjectName("sectionDescription"); source_note.setWordWrap(True); config_layout.addWidget(source_note)
        source_row = QHBoxLayout()
        self.layer31_source_component = QComboBox(); self.layer31_source_component.addItem("Use current validated Layer 1 plan", None)
        self.layer31_source_model = QComboBox(); self.layer31_source_model.addItem("Repeated identical fractions", "identical_fractions"); self.layer31_source_model.addItem("Explicit per-fraction validated doses", "explicit_fraction_doses")
        add_source = QPushButton("Add validated component source"); add_source.clicked.connect(self._add_layer31_component_source)
        remove_source = QPushButton("Remove selected source"); remove_source.clicked.connect(self._remove_layer31_component_source)
        source_row.addWidget(self.layer31_source_component, 2); source_row.addWidget(self.layer31_source_model, 2); source_row.addWidget(add_source); source_row.addWidget(remove_source)
        config_layout.addLayout(source_row)
        self.layer31_component_table = _table(["Component", "Fraction-dose model", "Validated Layer 1 source(s)"])
        self.layer31_component_table.setMaximumHeight(150); config_layout.addWidget(self.layer31_component_table)
        roi_title = QLabel("Step 4 — Assign identity-bound tissue parameters")
        roi_title.setObjectName("sectionTitle"); config_layout.addWidget(roi_title)
        roi_note = QLabel("Only rasterised Layer 1 ROIs are eligible. Every assignment requires a positive α/β value and explicit parameter provenance.")
        roi_note.setObjectName("sectionDescription"); roi_note.setWordWrap(True); config_layout.addWidget(roi_note)
        roi_form = QHBoxLayout()
        self.layer31_roi_selector = QComboBox(); self.layer31_roi_selector.setMinimumWidth(250)
        self.layer31_alpha_beta = QLineEdit(); self.layer31_alpha_beta.setPlaceholderText("α/β (Gy)")
        self.layer31_parameter_source_type = QComboBox(); self.layer31_parameter_source_type.addItem("User-declared", "user_selected"); self.layer31_parameter_source_type.addItem("Protocol / configured reference", "configured_reference"); self.layer31_parameter_source_type.addItem("Imported literature parameter set", "imported_parameter_set")
        self.layer31_parameter_source = QLineEdit("User-declared exploratory tissue parameter"); self.layer31_parameter_source.setPlaceholderText("Source / citation")
        self.layer31_parameter_set = QLineEdit("manual-v1"); self.layer31_parameter_set.setPlaceholderText("Parameter-set ID")
        self.layer31_parameter_source_type.currentIndexChanged.connect(self._update_layer31_tissue_source_defaults)
        add_roi = QPushButton("Add / replace tissue assignment"); add_roi.clicked.connect(self._add_layer31_roi_assignment)
        roi_form.addWidget(self.layer31_roi_selector, 2); roi_form.addWidget(self.layer31_alpha_beta)
        roi_form.addWidget(self.layer31_parameter_source_type); roi_form.addWidget(self.layer31_parameter_source, 2)
        roi_form.addWidget(self.layer31_parameter_set); roi_form.addWidget(add_roi)
        config_layout.addLayout(roi_form)
        self.layer31_roi_table = _table(["ROI", "ROI number", "α/β (Gy)", "Source type", "Source", "Parameter set"])
        self.layer31_roi_table.setMaximumHeight(190); config_layout.addWidget(self.layer31_roi_table)
        remove_roi = QPushButton("Remove selected assignment"); remove_roi.clicked.connect(self._remove_layer31_roi_assignment)
        config_layout.addWidget(remove_roi, 0, Qt.AlignLeft)

        warning_card, warning_layout = self._card(
            "Step 5 — Configure the optional 3.1A LQ high-dose warning",
            "The criterion marks model-domain extrapolation only. BED/EQD2 continues on both sides and never switches to MLQ.",
        )
        warning_row = QHBoxLayout()
        self.layer31_high_dose_criterion = QComboBox()
        self.layer31_high_dose_criterion.addItem("Not configured", "not_configured")
        self.layer31_high_dose_criterion.addItem("Custom operational threshold…", "custom_operational")
        self.layer31_high_dose_criterion.addItem("Literature-defined sensitivity threshold…", "literature_sensitivity")
        self.layer31_high_dose_threshold = QLineEdit(); self.layer31_high_dose_threshold.setPlaceholderText("Gy per fraction")
        self.layer31_high_dose_source = QLineEdit(); self.layer31_high_dose_source.setPlaceholderText("Threshold source / rationale")
        warning_row.addWidget(QLabel("LQ high-dose warning criterion")); warning_row.addWidget(self.layer31_high_dose_criterion, 2)
        warning_row.addWidget(self.layer31_high_dose_threshold); warning_row.addWidget(self.layer31_high_dose_source, 2)
        warning_layout.addLayout(warning_row); config_layout.addWidget(warning_card)
        self.layer31_high_dose_criterion.currentIndexChanged.connect(self._update_layer31_high_dose_controls)

        tumour_card, tumour_layout = self._layer31_model_editor("Step 6 — Configure the 3.1B tumour model", "tumour")
        normal_card, normal_layout = self._layer31_model_editor("Step 7 — Configure the 3.1C normal-cell model only when required", "normal_cell")
        self.layer31_tumour_kinetics = tumour_layout
        self.layer31_normal_kinetics = normal_layout
        self.layer31_tumour_scenario = tumour_layout["scenario"]
        self.layer31_normal_scenario = normal_layout["scenario"]
        self._update_layer31_model_preset("tumour"); self._update_layer31_model_preset("normal_cell")
        config_layout.addWidget(tumour_card); config_layout.addWidget(normal_card)
        comparator_title = QLabel("Step 8 — Define an explicit therapeutic-ratio comparator when 3.1C is required")
        comparator_title.setObjectName("sectionTitle"); config_layout.addWidget(comparator_title)
        comparator = QHBoxLayout()
        self.layer31_tr_enabled = QCheckBox("Define therapeutic-ratio comparator schedule")
        self.layer31_tr_fraction_count = QLineEdit(); self.layer31_tr_fraction_count.setPlaceholderText("Fractions")
        self.layer31_tr_delivery_time = QLineEdit(); self.layer31_tr_delivery_time.setPlaceholderText("Delivery time per fraction")
        self.layer31_tr_source = QLineEdit(); self.layer31_tr_source.setPlaceholderText("Comparator source / protocol")
        comparator.addWidget(self.layer31_tr_enabled); comparator.addWidget(self.layer31_tr_fraction_count)
        comparator.addWidget(self.layer31_tr_delivery_time); comparator.addWidget(self.layer31_tr_source, 1)
        config_layout.addLayout(comparator)
        self.layer31_tr_note = QLabel(
            "Disabled unless 3.1C is explicitly configured. Sequential mixed-fraction LRT+cERT requires a protocol-defined comparator; ASCEND does not invent one."
        )
        self.layer31_tr_note.setObjectName("sectionDescription"); self.layer31_tr_note.setWordWrap(True)
        config_layout.addWidget(self.layer31_tr_note)
        self.layer31_tr_enabled.toggled.connect(self._update_layer31_tr_controls)
        paired_row = QHBoxLayout()
        self.layer31_paired_course_path = QLineEdit(); self.layer31_paired_course_path.setReadOnly(True)
        self.layer31_paired_course_path.setPlaceholderText("Optional prior Layer 3.1 result for formal LRT versus LRT+cERT comparison")
        paired_select = QPushButton("Select paired-course result…")
        paired_clear = QPushButton("Clear")
        paired_select.clicked.connect(self._select_layer31_paired_course_result)
        paired_clear.clicked.connect(self.layer31_paired_course_path.clear)
        paired_row.addWidget(QLabel("Paired-course reference")); paired_row.addWidget(self.layer31_paired_course_path, 1)
        paired_row.addWidget(paired_select); paired_row.addWidget(paired_clear)
        config_layout.addLayout(paired_row)
        tcp_title = QLabel("Layer 3.1D — Configure direct-clonogenic Poisson TCP")
        tcp_title.setObjectName("sectionTitle"); config_layout.addWidget(tcp_title)
        tcp_note = QLabel("TCP consumes the Layer 3.1B MLQ survival field. It does not recalculate dose, MLQ, EUD, masks, or geometry. Parameters are research-model inputs and are not clinically validated.")
        tcp_note.setObjectName("sectionDescription"); tcp_note.setWordWrap(True); config_layout.addWidget(tcp_note)
        tcp_grid = QGridLayout()
        self.layer31_tcp_density = QLineEdit(); self.layer31_tcp_density.setPlaceholderText("Required clonogens/cm3")
        self.layer31_tcp_units = QComboBox(); self.layer31_tcp_units.addItem("clonogens/cm3")
        self.layer31_tcp_source = QLineEdit(); self.layer31_tcp_source.setPlaceholderText("Required source")
        self.layer31_tcp_parameter_set = QLineEdit(); self.layer31_tcp_parameter_set.setPlaceholderText("Required parameter-set ID")
        tcp_grid.addWidget(QLabel("Clonogen density"), 0, 0); tcp_grid.addWidget(self.layer31_tcp_density, 0, 1)
        tcp_grid.addWidget(QLabel("Units"), 0, 2); tcp_grid.addWidget(self.layer31_tcp_units, 0, 3)
        tcp_grid.addWidget(QLabel("Source"), 1, 0); tcp_grid.addWidget(self.layer31_tcp_source, 1, 1)
        tcp_grid.addWidget(QLabel("Parameter-set ID"), 1, 2); tcp_grid.addWidget(self.layer31_tcp_parameter_set, 1, 3)
        self.layer31_tcp_repopulation = QCheckBox("Apply delayed exponential repopulation")
        self.layer31_tcp_overall_time = QLineEdit(); self.layer31_tcp_overall_time.setPlaceholderText("Overall treatment time (days)")
        self.layer31_tcp_kickoff = QLineEdit(); self.layer31_tcp_kickoff.setPlaceholderText("Kick-off time Tk (days)")
        self.layer31_tcp_doubling = QLineEdit(); self.layer31_tcp_doubling.setPlaceholderText("Potential doubling time Tpot (days)")
        tcp_grid.addWidget(self.layer31_tcp_repopulation, 2, 0, 1, 2); tcp_grid.addWidget(self.layer31_tcp_overall_time, 2, 2)
        tcp_grid.addWidget(self.layer31_tcp_kickoff, 2, 3); tcp_grid.addWidget(self.layer31_tcp_doubling, 2, 4)
        self.layer31_tcp_sensitivity = QCheckBox("Enable clonogen-density sensitivity")
        self.layer31_tcp_sensitivity_values = QLineEdit(); self.layer31_tcp_sensitivity_values.setPlaceholderText("Comma-separated positive densities")
        tcp_grid.addWidget(self.layer31_tcp_sensitivity, 3, 0, 1, 2); tcp_grid.addWidget(self.layer31_tcp_sensitivity_values, 3, 2, 1, 3)
        config_layout.addLayout(tcp_grid)
        self.layer31_tcp_repopulation.toggled.connect(self._update_layer31_tcp_controls)
        self.layer31_tcp_sensitivity.toggled.connect(self._update_layer31_tcp_controls)
        self._update_layer31_tcp_controls()
        self._update_layer31_high_dose_controls(); self._update_layer31_tr_controls(False)
        run_title = QLabel("Step 9 — Save configuration and run the gated workflow")
        run_title.setObjectName("sectionTitle"); config_layout.addWidget(run_title)
        run_note = QLabel("A blocked or incomplete branch remains visibly blocked. ASCEND does not create normal-looking placeholder biological results.")
        run_note.setObjectName("sectionDescription"); run_note.setWordWrap(True); config_layout.addWidget(run_note)
        save = QPushButton("Save biological configuration"); save.clicked.connect(self._save_configuration); config_layout.addWidget(save, 0, Qt.AlignLeft)
        overview_layout.addWidget(config_card)
        self.layer31_tabs.addTab(overview, "1–9 Configure / run")

        spatial = QWidget(); spatial_layout = QVBoxLayout(spatial)
        map_title = QLabel("Steps 10–13 — MAP · primary Layer 3.1A output")
        map_title.setObjectName("sectionTitle"); spatial_layout.addWidget(map_title)
        map_order = QLabel(
            "10 Select physical dose, s-BED, s-EQD2, survival, or model-effect field.  "
            "11 Navigate linked axial/sagittal/coronal views.  12 Toggle validated GTV, vertex, valley, and OAR anatomy.  "
            "13 Inspect the same field on the interactive 3D CAD surface and export STL/VTP presentation artifacts."
        )
        map_order.setObjectName("sectionDescription"); map_order.setWordWrap(True); spatial_layout.addWidget(map_order)
        self.layer31a_warning = WarningBanner(); spatial_layout.addWidget(self.layer31a_warning)
        viewer_card, self.layer31_viewer_layout = self._card("Primary spatial output", "The map is the major 3.1A output. Anatomy, crosshair, masks, camera and reporting layout remain fixed while the stored physical or biological field changes.")
        self.layer31_viewer_status = QLabel("Run Layer 3.1, then build the hash-verified field viewer.")
        self.layer31_viewer_status.setObjectName("sectionDescription"); self.layer31_viewer_status.setWordWrap(True)
        self.layer31_viewer_layout.addWidget(self.layer31_viewer_status); spatial_layout.addWidget(viewer_card, 1)
        lq_table_title = QLabel("Stored 3.1A ROI summaries")
        lq_table_title.setObjectName("sectionTitle"); spatial_layout.addWidget(lq_table_title)
        self.layer31a_table = _table(["ROI", "α/β (Gy)", "s-BED mean", "s-BED D95", "s-BED D50", "s-EQD2 mean", "s-EQD2 D95", "Flagged %"])
        self.layer31a_table.setMaximumHeight(190); spatial_layout.addWidget(self.layer31a_table)
        self.layer31_tabs.addTab(spatial, "10–13 Map")

        survival = QWidget(); survival_layout = QVBoxLayout(survival)
        whole_title = QLabel("Step 14 — WHOLE-TUMOUR RESULT")
        whole_title.setObjectName("sectionTitle"); survival_layout.addWidget(whole_title)
        whole_note = QLabel("For 3.1B, mean tumour surviving fraction SFᵀ and tumour EUDᵀ are the major numerical outputs. The spatial survival map explains how those whole-tumour values arise.")
        whole_note.setObjectName("sectionDescription"); whole_note.setWordWrap(True); survival_layout.addWidget(whole_note)
        self.layer31b_summary = _table(["Result", "Value", "Units / state", "Evidence"]); survival_layout.addWidget(self.layer31b_summary)
        self.layer31b_comparison = _table(["Paired-course output", "Value", "State", "Evidence"]); survival_layout.addWidget(self.layer31b_comparison)
        self.layer31_tabs.addTab(survival, "14 Whole-tumour SF / EUD")

        regional = QWidget(); regional_layout = QVBoxLayout(regional)
        regional_title = QLabel("Step 15 — REGIONAL EXPLANATION")
        regional_title.setObjectName("sectionTitle"); regional_layout.addWidget(regional_title)
        regional_note = QLabel("The primary regional visual is the 100% residual-survival contribution bar in the unified viewer. Selecting its vertex, valley, or other-GTV segment focuses the corresponding validated mask in both 2D and 3D.")
        regional_note.setObjectName("sectionDescription"); regional_note.setWordWrap(True); regional_layout.addWidget(regional_note)
        self.layer31b_regional = _table(["Region", "Voxel count", "Tumour volume fraction", "Mean surviving fraction", "Survivor contribution φ"]); regional_layout.addWidget(self.layer31b_regional)
        self.layer31b_hf_reconciliation = _table(["High-dose fraction representation", "Value (%)", "Basis", "Difference (percentage points)"]); regional_layout.addWidget(self.layer31b_hf_reconciliation)
        self.layer31_tabs.addTab(regional, "15 Regional explanation")

        ratio = QWidget(); ratio_layout = QVBoxLayout(ratio)
        ratio_title = QLabel("Step 16 — Recalculate declared sensitivity scenarios and assess gated therapeutic ratio")
        ratio_title.setObjectName("sectionTitle"); ratio_layout.addWidget(ratio_title)
        self.layer31c_summary = _table(["Result", "Value", "Applicability", "Comparator"]); ratio_layout.addWidget(self.layer31c_summary)
        matrix_note = QLabel("C1–C3 and N1–N3 are standardised sensitivity scenarios, not patient-specific radiosensitivity estimates."); matrix_note.setWordWrap(True); matrix_note.setObjectName("sectionDescription")
        ratio_layout.addWidget(matrix_note)
        self.layer31c_matrix = _table(["Tumour scenario", "Normal scenario", "TR", "Tumour EUD (Gy)", "Normal SF actual", "Normal SF reference", "Applicability"])
        ratio_layout.addWidget(self.layer31c_matrix)
        self.layer31_tabs.addTab(ratio, "16 Scenarios / therapeutic ratio")

        tcp = QWidget(); tcp_layout = QVBoxLayout(tcp)
        tcp_title = QLabel("Layer 3.1D — Spatial MLQ-Poisson tumour control probability")
        tcp_title.setObjectName("sectionTitle"); tcp_layout.addWidget(tcp_title)
        tcp_scope = QLabel("RESEARCH MODEL · BIOLOGICALLY UNVALIDATED · DIRECT RADIATION KILL ONLY · NOT A CLINICAL OUTCOME PREDICTION")
        tcp_scope.setObjectName("sectionDescription"); tcp_scope.setWordWrap(True); tcp_layout.addWidget(tcp_scope)
        self.layer31d_warning = WarningBanner(); tcp_layout.addWidget(self.layer31d_warning)
        self.layer31d_summary = _table(["Endpoint", "Value", "Units / state", "Interpretation"]); tcp_layout.addWidget(self.layer31d_summary)
        self.layer31d_comparison = _table(["Model", "TCP", "ln(TCP)", "Expected surviving clonogens"]); tcp_layout.addWidget(self.layer31d_comparison)
        self.layer31d_spatial = _table(["Region", "Volume (cm3)", "Mean MLQ survival", "Residual clonogens", "Residual fraction", "P0"]); tcp_layout.addWidget(self.layer31d_spatial)
        self.layer31d_sensitivity = _table(["Parameter", "Value", "Radiation-only TCP", "Expected surviving clonogens"]); tcp_layout.addWidget(self.layer31d_sensitivity)
        provenance_label = QLabel("Dependency provenance and model assumptions"); provenance_label.setObjectName("sectionTitle"); tcp_layout.addWidget(provenance_label)
        self.layer31d_provenance = _text_view(); tcp_layout.addWidget(self.layer31d_provenance)
        self.layer31_tabs.addTab(tcp, "3.1D TCP")

        provenance = QWidget(); provenance_layout = QVBoxLayout(provenance)
        provenance_title = QLabel("Step 17 — Audit provenance, validation state, hashes, and exports")
        provenance_title.setObjectName("sectionTitle"); provenance_layout.addWidget(provenance_title)
        self.layer31_provenance = _text_view(); provenance_layout.addWidget(self.layer31_provenance)
        self.layer31_tabs.addTab(provenance, "17 Provenance / export")

    def _layer31_model_editor(self, title: str, tissue: str) -> tuple[QFrame, dict[str, Any]]:
        """Build one preset-driven MLQ editor with explicit provenance."""
        description = (
            "C1–C3 are tumour sensitivity scenarios. The Zhang 2022 tumour kinetic preset is explicit; delivery time remains treatment-derived or user supplied."
            if tissue == "tumour" else
            "N1–N3 set SF2, α/β, α and β only. Normal-cell δ and repair half-time remain incomplete unless a defined normal preset or sourced custom model is selected."
        )
        card, layout = self._card(title, description)
        grid = QGridLayout()
        model = QLineEdit("Guerrero–Li MLQ"); model.setReadOnly(True); model.setMinimumWidth(155); model.setCursorPosition(0)
        scenario = QComboBox(); scenario.addItems(["Not configured", *(TUMOUR_SCENARIOS if tissue == "tumour" else NORMAL_SCENARIOS)])
        kinetic_preset = QComboBox(); kinetic_preset.setMinimumWidth(250); kinetic_preset.addItem("Not configured", "not_configured")
        presets = TUMOUR_KINETIC_PRESETS if tissue == "tumour" else NORMAL_KINETIC_PRESETS
        for preset_id, preset in presets.items(): kinetic_preset.addItem(str(preset["label"]), preset_id)
        kinetic_preset.addItem("Custom sourced kinetic parameters…", "custom")
        grid.addWidget(QLabel("Model"), 0, 0); grid.addWidget(model, 0, 1)
        grid.addWidget(QLabel("Scenario"), 0, 2); grid.addWidget(scenario, 0, 3)
        grid.addWidget(QLabel("Kinetic preset"), 0, 4); grid.addWidget(kinetic_preset, 0, 5)
        fields: dict[str, Any] = {"model": model, "scenario": scenario, "kinetic_preset": kinetic_preset}
        for column, (key, label) in enumerate((("alpha_beta_gy", "α/β (Gy)"), ("sf2", "SF2"), ("alpha_per_gy", "α (Gy⁻¹)"), ("beta_per_gy2", "β (Gy⁻²)"))):
            widget = QLineEdit(); widget.setReadOnly(True); widget.setPlaceholderText("Select scenario")
            grid.addWidget(QLabel(label), 1, column * 2); grid.addWidget(widget, 1, column * 2 + 1); fields[key] = widget
        delta = QLineEdit(); delta.setPlaceholderText("δ (Gy⁻¹)")
        half_time = QLineEdit(); half_time.setPlaceholderText("Repair half-time")
        delivery = QLineEdit(); delivery.setPlaceholderText("Required delivery time")
        unit = QComboBox(); unit.addItems(["minutes", "seconds", "hours"])
        grid.addWidget(QLabel("δ (Gy⁻¹)"), 2, 0); grid.addWidget(delta, 2, 1)
        grid.addWidget(QLabel("Repair half-time"), 2, 2); grid.addWidget(half_time, 2, 3)
        grid.addWidget(QLabel("Delivery time"), 2, 4); grid.addWidget(delivery, 2, 5); grid.addWidget(unit, 2, 6)
        source = QLineEdit(); source.setPlaceholderText("Required source / citation")
        set_id = QLineEdit(); set_id.setPlaceholderText("Required parameter-set ID")
        grid.addWidget(QLabel("Parameter source"), 3, 0); grid.addWidget(source, 3, 1, 1, 3)
        grid.addWidget(QLabel("Parameter-set ID"), 3, 4); grid.addWidget(set_id, 3, 5, 1, 2)
        status = QLabel(); status.setWordWrap(True); status.setObjectName("sectionDescription")
        grid.addWidget(status, 4, 0, 1, 8)
        fields.update({"parameter_set_id": set_id, "parameter_source": source, "delta_per_gy": delta,
                       "repair_half_time": half_time, "treatment_delivery_time": delivery,
                       "time_unit": unit, "status": status, "tissue": tissue})
        scenario.currentIndexChanged.connect(lambda _index, kind=tissue: self._update_layer31_model_preset(kind))
        kinetic_preset.currentIndexChanged.connect(lambda _index, kind=tissue: self._update_layer31_model_preset(kind))
        layout.addLayout(grid)
        return card, fields

    def _update_layer31_high_dose_controls(self, _index: int = -1) -> None:
        """Keep the warning criterion visibly optional and provenance-bound."""
        mode = str(self.layer31_high_dose_criterion.currentData())
        configured = mode != "not_configured"
        self.layer31_high_dose_threshold.setEnabled(configured)
        self.layer31_high_dose_source.setEnabled(configured)
        if not configured and not getattr(self, "_loading_configuration", False):
            self.layer31_high_dose_threshold.clear(); self.layer31_high_dose_source.clear()
        if mode == "custom_operational":
            self.layer31_high_dose_source.setPlaceholderText("Operational rationale (not a biological cutoff)")
        elif mode == "literature_sensitivity":
            self.layer31_high_dose_source.setPlaceholderText("Required citation and reproduction context")

    def _update_layer31_tissue_source_defaults(self, _index: int = -1) -> None:
        """Supply honest manual provenance while requiring explicit external sources."""
        source_type = str(self.layer31_parameter_source_type.currentData())
        if source_type == "user_selected":
            if not self.layer31_parameter_source.text().strip():
                self.layer31_parameter_source.setText("User-declared exploratory tissue parameter")
            if not self.layer31_parameter_set.text().strip():
                self.layer31_parameter_set.setText("manual-v1")
        elif self.layer31_parameter_source.text().strip() == "User-declared exploratory tissue parameter":
            self.layer31_parameter_source.clear(); self.layer31_parameter_set.clear()

    def _update_layer31_model_preset(self, tissue: str) -> None:
        """Populate named scenario values without conflating tumour and normal kinetics."""
        editor = self.layer31_tumour_kinetics if tissue == "tumour" else self.layer31_normal_kinetics
        scenario_id = editor["scenario"].currentText()
        scenarios = TUMOUR_SCENARIOS if tissue == "tumour" else NORMAL_SCENARIOS
        presets = TUMOUR_KINETIC_PRESETS if tissue == "tumour" else NORMAL_KINETIC_PRESETS
        if scenario_id in scenarios:
            scenario = scenarios[scenario_id]
            for key in ("alpha_beta_gy", "sf2", "alpha_per_gy", "beta_per_gy2"):
                editor[key].setText(str(scenario[key]))
            # A named tumour scenario uses the documented GRID tumour kinetic
            # preset by default.  N1–N3 deliberately do not select a kinetic
            # preset because the normal-cell formalism must be declared.
            if tissue == "tumour" and editor["kinetic_preset"].currentData() == "not_configured":
                index = editor["kinetic_preset"].findData("zhang_grid_2022")
                editor["kinetic_preset"].blockSignals(True); editor["kinetic_preset"].setCurrentIndex(index); editor["kinetic_preset"].blockSignals(False)
        else:
            for key in ("alpha_beta_gy", "sf2", "alpha_per_gy", "beta_per_gy2"):
                editor[key].clear()

        preset_id = str(editor["kinetic_preset"].currentData())
        custom = preset_id == "custom"
        preset = presets.get(preset_id)
        previously_locked = editor["parameter_source"].isReadOnly()
        for key in ("delta_per_gy", "repair_half_time"):
            editor[key].setReadOnly(not custom)
        editor["parameter_source"].setReadOnly(not custom)
        editor["parameter_set_id"].setReadOnly(not custom)
        if preset:
            editor["delta_per_gy"].setText(str(preset["delta_per_gy"]))
            editor["repair_half_time"].setText(str(preset["repair_half_time"]))
            editor["time_unit"].setCurrentText(str(preset["time_unit"]))
            editor["parameter_source"].setText(str(preset["parameter_source"]))
            editor["parameter_set_id"].setText(f"{preset['parameter_set_id']}-{scenario_id.lower()}" if scenario_id in scenarios else str(preset["parameter_set_id"]))
            editor["status"].setText("PRESET · scenario and kinetic provenance locked. Delivery time remains required and case-specific.")
        elif custom:
            if previously_locked or editor["parameter_source"].text().startswith("Scenario only"):
                editor["delta_per_gy"].clear(); editor["repair_half_time"].clear()
                editor["parameter_source"].clear(); editor["parameter_set_id"].clear()
            editor["status"].setText("CUSTOM · δ, repair half-time, source, parameter-set ID, and delivery time are required.")
        else:
            editor["delta_per_gy"].clear(); editor["repair_half_time"].clear()
            source = SCENARIO_SOURCE[tissue]
            if scenario_id in scenarios:
                editor["parameter_source"].setText(f"Scenario only: {source['citation']}; kinetic model not configured")
                editor["parameter_set_id"].setText(f"{source['parameter_set_prefix']}-{scenario_id.lower()}-scenario-only")
            else:
                editor["parameter_source"].clear(); editor["parameter_set_id"].clear()
            editor["status"].setText("INCOMPLETE · select a defined kinetic preset or custom sourced kinetics before calculation.")
        editor["time_unit"].setEnabled(custom or not preset)

    def _update_layer31_tr_controls(self, checked: bool | None = None) -> None:
        """Expose comparator inputs only when 3.1C is explicitly enabled."""
        enabled = self.layer31_tr_enabled.isChecked() if checked is None else bool(checked)
        for widget in (self.layer31_tr_fraction_count, self.layer31_tr_delivery_time, self.layer31_tr_source):
            widget.setEnabled(enabled)
        self.layer31_tr_note.setText(
            "Comparator enabled. Define the uniform reference schedule and its protocol source explicitly."
            if enabled else
            "Comparator disabled. Sequential mixed-fraction LRT+cERT returns TR_REFERENCE_SCHEDULE_UNDEFINED / NOT_APPLICABLE; ASCEND does not invent a schedule."
        )

    def _select_layer31_paired_course_result(self) -> None:
        """Select a prior result; comparison gates are enforced by the service."""
        start = str(self.controller.case.root if self.controller.case else Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select paired Layer 3.1 result", start,
            "Layer 3.1 results (*.json);;JSON files (*.json)",
        )
        if path:
            self.layer31_paired_course_path.setText(path)

    def _update_layer31_tcp_controls(self, _checked: bool | None = None) -> None:
        """Expose only TCP inputs that are active in the selected model."""
        repopulation = self.layer31_tcp_repopulation.isChecked()
        for widget in (self.layer31_tcp_overall_time, self.layer31_tcp_kickoff, self.layer31_tcp_doubling):
            widget.setEnabled(repopulation)
        self.layer31_tcp_sensitivity_values.setEnabled(self.layer31_tcp_sensitivity.isChecked())

    def _build_review_page(self) -> None:
        _, layout = self._new_page("Review", "Calculation state, interpretation state, warnings, and provenance remain separate.")
        self.review_text = _text_view()
        layout.addWidget(self.review_text, 1)

    def _build_export_page(self) -> None:
        _, layout = self._new_page("Export", "JSON is authoritative. CSV files are rendered from stored result objects without recalculation.")
        button = QPushButton("Export JSON and CSV")
        button.setObjectName("primary")
        button.clicked.connect(self._export)
        layout.addWidget(button, 0, Qt.AlignLeft)
        self.export_path = QLabel("Open a case first")
        self.export_path.setWordWrap(True)
        layout.addWidget(self.export_path)
        self.export_result = _text_view()
        layout.addWidget(self.export_result, 1)

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
                self.eclipse_import_status.setText(
                    f"Selected Eclipse reference: {path}. Endpoint mapping will run after DICOM import."
                )

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

    @staticmethod
    def _number(text: str) -> float | None:
        return float(text) if text.strip() else None

    @staticmethod
    def _integer(text: str) -> int | None:
        return int(text) if text.strip() else None

    @staticmethod
    def _endpoint_label(item: dict[str, Any]) -> str:
        value = float(item.get("value", 0))
        kind = item.get("kind")
        if kind == "d_percent":
            return f"D{value:g}"
        if kind == "coverage_relative_rx":
            return f"V{value * 100:g}%Rx"
        return f"V{value:g}Gy"

    def _refresh_protocol_endpoint_table(self) -> None:
        _set_table(self.protocol_endpoint_table, [[
            item.get("role"), self._endpoint_label(item), item.get("value"),
            "Eclipse auto-fill" if item.get("source") == "eclipse_reference_auto_fill" else "User selected",
        ] for item in self._protocol_endpoint_entries], "No optional protocol endpoints selected.")

    def _add_protocol_endpoint(self) -> None:
        try:
            value = float(self.protocol_endpoint_value.text())
            if not math.isfinite(value) or value <= 0:
                raise ValueError
            kind = str(self.protocol_endpoint_kind.currentData())
            if kind == "d_percent" and value > 100:
                raise ValueError
        except ValueError:
            QMessageBox.critical(self, "ASCEND configuration", "Enter a positive endpoint value. Dxx must not exceed 100%.")
            return
        record = protocol_endpoint_record(self.protocol_endpoint_role.currentText(), kind, value)
        semantic = (record["role"], record["kind"], float(record["value"]))
        if any((item["role"], item["kind"], float(item["value"])) == semantic for item in self._protocol_endpoint_entries):
            return
        self._protocol_endpoint_entries.append(record)
        self._refresh_protocol_endpoint_table()

    def _remove_protocol_endpoint(self) -> None:
        row = self.protocol_endpoint_table.currentRow()
        if 0 <= row < len(self._protocol_endpoint_entries):
            self._protocol_endpoint_entries.pop(row)
            self._refresh_protocol_endpoint_table()

    def _prefill_protocol_endpoints(self, silent: bool = False) -> bool:
        """Persist the selected reference, map supported endpoints, and refresh the table."""
        case = self.controller.case
        source = self.tps_csv.text().strip()
        if self._loading_configuration:
            return False
        if not case:
            message = "Import or open an ASCEND case before mapping Eclipse endpoints."
            self.eclipse_import_status.setText(message)
            if not silent:
                QMessageBox.critical(self, "ASCEND Eclipse endpoint mapping", message)
            return False
        if not source:
            message = "No Eclipse DVH reference selected. Select a CSV/TXT file or Eclipse export folder on Import."
            self.eclipse_import_status.setText(message)
            if not silent:
                QMessageBox.critical(self, "ASCEND Eclipse endpoint mapping", message)
            return False
        try:
            configuration = CaseConfiguration.from_dict(case.configuration.to_dict())
            configuration.tps_metrics_csv = source
            configuration.protocol_native_endpoints = [dict(item) for item in self._protocol_endpoint_entries]
            self.controller.configure(configuration)
            suggestions = self.controller.prefill_eclipse_endpoints()
            self._protocol_endpoint_entries = [dict(item) for item in case.configuration.protocol_native_endpoints]
            self._refresh_protocol_endpoint_table()
            if not silent:
                QMessageBox.information(
                    self, "ASCEND Eclipse endpoint mapping",
                    f"Mapped {len(suggestions)} supported endpoint definition(s). Existing selections were retained.",
                )
            summary = case.configuration.eclipse_endpoint_prefill
            self.eclipse_import_status.setText(
                f"Imported {summary.get('supplied_record_count', 0)} Eclipse record(s); "
                f"{summary.get('added_endpoint_count', 0)} protocol endpoint(s) added."
            )
            self._pending_eclipse_reference = None
            self._prefill_oar_geometry(eclipse_only=True)
            return True
        except Exception as exc:
            self.eclipse_import_status.setText(f"Eclipse endpoint mapping failed: {exc}")
            self.activity.setText("BLOCKED")
            if not silent:
                QMessageBox.critical(self, "ASCEND Eclipse endpoint mapping", str(exc))
            return False

    def _add_treatment_component(self) -> None:
        """Validate and stage one structured treatment component without JSON entry."""
        component_id = self.component_id.text().strip()
        if not component_id:
            QMessageBox.critical(self, "ASCEND treatment context", "Component ID is required.")
            return
        try:
            component = TreatmentComponent(
                component_id=component_id,
                component_type=str(self.component_type.currentText()),
                dose_object_uid=self.component_dose_uid.text().strip() or None,
                plan_uid=self.component_plan_uid.text().strip() or None,
                fraction_count=self._integer(self.component_fractions.text()),
                prescription_gy=self._number(self.component_prescription.text()),
                rx_low_gy=self._number(self.component_rx_low.text()),
                rx_high_gy=self._number(self.component_rx_high.text()),
                source="user_supplied",
                start_time=self.component_start.text().strip() or None,
                end_time=self.component_end.text().strip() or None,
                preceding_gap_days=self._number(self.component_gap.text()),
                geometry_id=self.component_geometry.text().strip() or None,
                geometry_hash=self.component_geometry.text().strip() or None,
                prescription_source=self.component_prescription_source.text().strip() or None,
                provenance={
                    "entry_method": "qt_structured_treatment_component_editor",
                    "implicit_registration": False,
                    "implicit_dose_warping": False,
                },
            )
        except ValueError as exc:
            QMessageBox.critical(self, "ASCEND treatment context", str(exc))
            return
        self._treatment_component_entries = [
            item for item in self._treatment_component_entries
            if str(item.get("component_id")) != component_id
        ] + [component.to_dict()]
        self._refresh_treatment_component_table(select_component_id=component_id)
        self.footer_stage.setText("Treatment component staged; save configuration to apply")

    def _remove_treatment_component(self) -> None:
        """Remove the selected staged treatment component."""
        row = self.treatment_component_table.currentRow()
        if row < 0 or row >= len(self._treatment_component_entries):
            QMessageBox.information(self, "ASCEND treatment context", "Select a treatment component row to remove.")
            return
        del self._treatment_component_entries[row]
        self._refresh_treatment_component_table()
        self.footer_stage.setText("Treatment component removed; save configuration to apply")

    def _refresh_treatment_component_table(self, select_component_id: str | None = None) -> None:
        """Render staged component records and keep analysis selection identity stable."""
        selected = select_component_id or self.analysis_component.currentData()
        rows: list[list[Any]] = []
        for raw in self._treatment_component_entries:
            try:
                item = TreatmentComponent.from_dict(raw)
                rows.append([
                    item.component_id, item.component_type, item.prescription_gy, item.fraction_count,
                    item.dose_per_fraction_gy, item.rx_low_gy, item.rx_high_gy, item.start_time,
                    item.end_time, item.preceding_gap_days, item.prescription_source or item.source,
                    item.dose_object_uid, item.geometry_hash or item.geometry_id,
                ])
            except (TypeError, ValueError) as exc:
                rows.append([raw.get("component_id", "—"), "INVALID", "—", "—", "—", "—", "—", "—", "—", "—", str(exc), "—", "—"])
        _set_table(self.treatment_component_table, rows, "No treatment components configured.")
        self.analysis_component.blockSignals(True)
        self.analysis_component.clear()
        self.analysis_component.addItem("No component selected", None)
        for raw in self._treatment_component_entries:
            component_id = str(raw.get("component_id") or "")
            if component_id:
                self.analysis_component.addItem(component_id, component_id)
        index = self.analysis_component.findData(selected)
        self.analysis_component.setCurrentIndex(index if index >= 0 else 0)
        self.analysis_component.blockSignals(False)
        if hasattr(self, "layer31_source_component"):
            retained = self.layer31_source_component.currentData()
            self.layer31_source_component.clear()
            self.layer31_source_component.addItem("Use current validated Layer 1 plan", None)
            for raw in self._treatment_component_entries:
                component_id = str(raw.get("component_id") or "")
                if component_id:
                    self.layer31_source_component.addItem(component_id, component_id)
            source_index = self.layer31_source_component.findData(retained)
            self.layer31_source_component.setCurrentIndex(source_index if source_index >= 0 else 0)

    def _save_configuration(self, silent: bool = False) -> bool:
        case = self.controller.case
        if not case:
            if not silent:
                QMessageBox.critical(self, "ASCEND", "Import a case first.")
            return False
        try:
            roles: dict[str, str | list[str]] = {}
            for role, widget in self.role_widgets.items():
                value = widget.text().strip() if isinstance(widget, QLineEdit) else widget.currentText().strip()
                if value:
                    roles[role] = [item.strip() for item in value.split(",") if item.strip()] if role == "VTV_H_individual" else value
            fractions = self._integer(self.fractions.currentText())
            previous = case.configuration
            layer32_parameters = resolved_parameters(previous.layer32_parameters)
            layer32_parameters.update({
                "nonlocal_scaling": self._number(self.layer32_scaling.text()),
                "pde_steps": self._integer(self.layer32_steps.text()),
                "pde_dt": self._number(self.layer32_dt.text()),
                "model_grid_target_spacing_mm": self._number(self.layer32_grid_spacing.text()),
                "model_domain_margin_mm": self._number(self.layer32_margin.text()),
            })
            layer32_parameters = resolved_parameters(layer32_parameters)
            protocol_native_endpoints = [dict(item) for item in self._protocol_endpoint_entries]
            oar_structures = [dict(item) for item in self._oar_entries]
            tumour_scenario = self.layer31_tumour_scenario.currentText()
            normal_scenario = self.layer31_normal_scenario.currentText()
            tumour_parameters = self._layer31_kinetic_parameters(self.layer31_tumour_kinetics, tumour_scenario, "tumour")
            normal_parameters = self._layer31_kinetic_parameters(self.layer31_normal_kinetics, normal_scenario, "normal_cell")
            warning_mode = str(self.layer31_high_dose_criterion.currentData())
            warning_threshold = None
            warning_source = None
            if warning_mode != "not_configured":
                warning_threshold = self._number(self.layer31_high_dose_threshold.text())
                warning_source = self.layer31_high_dose_source.text().strip()
                if warning_threshold is None or warning_threshold <= 0:
                    raise ValueError("The Layer 3.1A warning criterion requires a positive Gy/fraction threshold.")
                if warning_mode == "literature_sensitivity" and not warning_source:
                    raise ValueError("A literature-defined warning criterion requires a citation and reproduction context.")
                if not warning_source:
                    warning_source = "user_defined_operational_warning_threshold_not_biological_cutoff"
            visualisation_settings = dict(previous.layer31_visualisation_settings)
            visualisation_settings["lq_high_dose_warning_criterion"] = {
                "mode": warning_mode, "threshold_gy_per_fraction": warning_threshold,
                "source": warning_source, "formalism_switching": False,
            }
            comparator_schedule: dict[str, Any] = {}
            if self.layer31_tr_enabled.isChecked():
                comparator_count = self._integer(self.layer31_tr_fraction_count.text())
                comparator_time = self._number(self.layer31_tr_delivery_time.text())
                if comparator_count is None or comparator_count <= 0 or comparator_time is None or comparator_time < 0:
                    raise ValueError("The therapeutic-ratio comparator requires positive fractions and a non-negative delivery time.")
                comparator_schedule = {
                    "schedule_type": "explicit_matched_uniform_schedule", "fraction_count": comparator_count,
                    "delivery_time": comparator_time,
                    "source": self.layer31_tr_source.text().strip() or "explicit_case_configuration",
                }
            tcp_parameters: dict[str, Any] = {}
            tcp_density_text = self.layer31_tcp_density.text().strip()
            if tcp_density_text or self.layer31_tcp_source.text().strip() or self.layer31_tcp_parameter_set.text().strip():
                tcp_density = self._number(tcp_density_text)
                if tcp_density is None or tcp_density <= 0:
                    raise ValueError("Layer 3.1D requires a positive clonogen density.")
                tcp_parameters = {
                    "clonogen_density_per_cm3": tcp_density,
                    "units": self.layer31_tcp_units.currentText(),
                    "source": self.layer31_tcp_source.text().strip(),
                    "parameter_set_id": self.layer31_tcp_parameter_set.text().strip(),
                    "repopulation_enabled": self.layer31_tcp_repopulation.isChecked(),
                    "sensitivity_enabled": self.layer31_tcp_sensitivity.isChecked(),
                }
                if self.layer31_tcp_repopulation.isChecked():
                    tcp_parameters.update({
                        "overall_treatment_time_days": self._number(self.layer31_tcp_overall_time.text()),
                        "kickoff_time_days": self._number(self.layer31_tcp_kickoff.text()),
                        "potential_doubling_time_days": self._number(self.layer31_tcp_doubling.text()),
                    })
                if self.layer31_tcp_sensitivity.isChecked():
                    try:
                        tcp_parameters["sensitivity_clonogen_density_values"] = [
                            float(item.strip()) for item in self.layer31_tcp_sensitivity_values.text().split(",") if item.strip()
                        ]
                    except ValueError as exc:
                        raise ValueError("Layer 3.1D sensitivity densities must be comma-separated numbers.") from exc
            configuration = CaseConfiguration(
                treatment_delivery_mode=self.mode.currentText(),
                treatment_approach=str(self.treatment_approach.currentData()),
                dose_context=self.dose_context.currentText(),
                prescriptions={
                    "Rx_L": Prescription(self._number(self.rx_l.currentText()), fractions, self.rx_l_source.currentText()),
                    "Rx_H": Prescription(self._number(self.rx_h.currentText()), fractions, self.rx_h_source.currentText()),
                },
                fractionation={"fractions": fractions} if fractions is not None else {},
                structure_roles=roles,
                validation_structures=[
                    item.strip() for item in self.validation_structures.text().split(",") if item.strip()
                ],
                protocol_id=self.protocol_id.text().strip() or None,
                protocol_context={
                    "prescriptions_confirmed": self.confirm_prescriptions.isChecked(),
                    "roles_confirmed": self.confirm_roles.isChecked(),
                    "dose_object_confirmed": self.confirm_dose.isChecked(),
                    "valley_confirmed": self.confirm_valley.isChecked(),
                },
                protocol_native_endpoints=protocol_native_endpoints,
                oar_structures=oar_structures,
                equal_prescriptions_protocol_confirmed=self.confirm_equal.isChecked(),
                partial_volume_only=self.mode.currentText() == "partial_volume_lrt",
                valley_definition_source=previous.valley_definition_source,
                valley_overlap_tolerance_pct=previous.valley_overlap_tolerance_pct,
                tps_metrics_csv=self.tps_csv.text().strip() or None,
                treatment_components=[dict(item) for item in self._treatment_component_entries],
                selected_treatment_component_id=self.analysis_component.currentData(),
                prescription_context=previous.prescription_context,
                supporting_outputs_enabled=self.supporting_outputs_enabled.isChecked(),
                supporting_output_categories=[
                    category for category, checkbox in self.supporting_output_checks.items() if checkbox.isChecked()
                ],
                layer31_roi_parameters=[dict(item) for item in self._layer31_roi_entries],
                layer31_component_sources=[dict(item) for item in self._layer31_component_entries],
                layer31_lq_high_dose_warning_gy_per_fraction=warning_threshold,
                layer31_mlq_tumour_parameters=tumour_parameters,
                layer31_mlq_normal_parameters=normal_parameters,
                layer31_tumour_scenario=None if tumour_scenario == "Not configured" else tumour_scenario,
                layer31_normal_scenario=None if normal_scenario == "Not configured" else normal_scenario,
                layer31_tr_reference_schedule=comparator_schedule,
                layer31_paired_course_reference_result_path=self.layer31_paired_course_path.text().strip() or None,
                layer31_tcp_parameters=tcp_parameters,
                layer31_visualisation_settings=visualisation_settings,
                layer31_materialise_full_maps_on_run=previous.layer31_materialise_full_maps_on_run,
                layer31_sensitivity_sweep_enabled=previous.layer31_sensitivity_sweep_enabled,
                layer31_sensitivity_sweep_mode=previous.layer31_sensitivity_sweep_mode,
                layer31_sensitivity_sweep_start=previous.layer31_sensitivity_sweep_start,
                layer31_sensitivity_sweep_end=previous.layer31_sensitivity_sweep_end,
                layer31_sensitivity_sweep_custom_values=previous.layer31_sensitivity_sweep_custom_values,
                layer32_enabled=self.layer32_enabled.isChecked(),
                layer32_parameters=layer32_parameters,
                eclipse_endpoint_prefill=previous.eclipse_endpoint_prefill,
            )
            self.controller.configure(configuration)
            self._pending_eclipse_reference = None
            self.activity.setText("Configuration saved")
            self.refresh()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "ASCEND configuration", str(exc))
            return False

    def _load_configuration(self) -> None:
        case = self.controller.case
        if not case:
            return
        config = case.configuration
        self._loading_configuration = True
        approach_index = self.treatment_approach.findData(config.treatment_approach)
        self.treatment_approach.setCurrentIndex(approach_index if approach_index >= 0 else self.treatment_approach.findData("UNKNOWN"))
        self.mode.setCurrentText(config.treatment_delivery_mode)
        self.dose_context.setCurrentText(config.dose_context)
        evidence = case.provenance.get("dicom_configuration_prefill", {})
        prescription_values = sorted({str(item.get("dose_gy")) for item in evidence.get("prescription_candidates", [])})
        fraction_values = sorted({str(item.get("fractions")) for item in evidence.get("fraction_candidates", [])}, key=lambda value: int(value))
        for widget, selected in (
            (self.rx_l, "" if config.prescriptions["Rx_L"].gy is None else str(config.prescriptions["Rx_L"].gy)),
            (self.rx_h, "" if config.prescriptions["Rx_H"].gy is None else str(config.prescriptions["Rx_H"].gy)),
        ):
            widget.clear(); widget.addItem(""); widget.addItems(prescription_values); widget.setCurrentText(selected)
        self.rx_l_source.setCurrentText(config.prescriptions["Rx_L"].source)
        self.rx_h_source.setCurrentText(config.prescriptions["Rx_H"].source)
        fractions = config.fractionation.get("fractions") or config.prescriptions["Rx_L"].fractions
        self.fractions.clear(); self.fractions.addItem(""); self.fractions.addItems(fraction_values)
        self.fractions.setCurrentText("" if fractions is None else str(fractions))
        self.dicom_prefill_summary.setText(
            f"RTPLAN {evidence.get('plan_label') or '—'}  ·  Dose {evidence.get('dose_summation_type') or '—'}  ·  "
            f"{evidence.get('beam_count', 0)} beam(s)  ·  "
            f"Status: {str(evidence.get('status', 'not available')).replace('_', ' ')}"
        )
        fraction_candidates = evidence.get("fraction_candidates", [])
        prescription_candidates = evidence.get("prescription_candidates", [])
        _set_table(self.dicom_fraction_candidates, [[
            item.get("fraction_group_number"),
            item.get("fractions"),
            item.get("referenced_beam_count"),
            item.get("source"),
        ] for item in fraction_candidates], "No RTPLAN fractionation candidates were found.")
        _set_table(self.dicom_prescription_candidates, [[
            item.get("dose_reference_number"),
            item.get("dose_gy"),
            item.get("label") or "Dose reference",
            item.get("referenced_roi_number") if item.get("referenced_roi_number") is not None else "—",
            " / ".join(filter(None, (
                str(item.get("dose_reference_type") or ""),
                str(item.get("dose_reference_structure_type") or ""),
            ))) or "—",
            item.get("source"),
        ] for item in prescription_candidates], "No RTPLAN prescription candidates were found.")
        warnings = [str(item).replace("_", " ") for item in evidence.get("warnings", [])]
        self.dicom_candidate_warnings.set_messages(warnings)
        self.protocol_id.setText(config.protocol_id or "")
        self._treatment_component_entries = [dict(item) for item in config.treatment_components]
        self._refresh_treatment_component_table(config.selected_treatment_component_id)
        self._protocol_endpoint_entries = [dict(item) for item in config.protocol_native_endpoints]
        self._refresh_protocol_endpoint_table()
        self._oar_entries = [dict(item) for item in config.oar_structures]
        self._refresh_oar_table()
        self.validation_structures.setText(", ".join(
            str(item.get("display_name") or item.get("roi_number")) for item in config.validation_structures
        ))
        retained_reference = config.tps_metrics_csv or self._pending_eclipse_reference or ""
        self.tps_csv.setText(retained_reference)
        self.eclipse_import_status.setText(
            f"Selected Eclipse reference: {retained_reference}" if retained_reference
            else "No Eclipse DVH reference selected."
        )
        context = config.protocol_context
        self.confirm_prescriptions.setChecked(bool(context.get("prescriptions_confirmed")))
        self.confirm_roles.setChecked(bool(context.get("roles_confirmed")))
        self.confirm_dose.setChecked(bool(context.get("dose_object_confirmed")))
        self.confirm_valley.setChecked(bool(context.get("valley_confirmed")))
        self.confirm_equal.setChecked(config.equal_prescriptions_protocol_confirmed)
        self.supporting_outputs_enabled.setChecked(config.supporting_outputs_enabled)
        for category, checkbox in self.supporting_output_checks.items():
            checkbox.setChecked(category in config.supporting_output_categories)
        self._toggle_supporting_output_controls(config.supporting_outputs_enabled)
        self.layer32_enabled.setChecked(config.layer32_enabled)
        self._update_layer32_enabled_controls(config.layer32_enabled)
        layer32 = resolved_parameters(config.layer32_parameters)
        self.layer32_scaling.setText(str(layer32["nonlocal_scaling"]))
        self.layer32_steps.setText(str(layer32["pde_steps"]))
        self.layer32_dt.setText(str(layer32["pde_dt"]))
        self.layer32_grid_spacing.setText(str(layer32["model_grid_target_spacing_mm"]))
        self.layer32_margin.setText(str(layer32["model_domain_margin_mm"]))
        self._layer31_roi_entries = [dict(item) for item in config.layer31_roi_parameters]
        self._refresh_layer31_roi_table()
        self._layer31_component_entries = [dict(item) for item in config.layer31_component_sources]
        self._refresh_layer31_component_table()
        criterion = dict(config.layer31_visualisation_settings.get("lq_high_dose_warning_criterion") or {})
        criterion_mode = str(criterion.get("mode") or (
            "custom_operational" if config.layer31_lq_high_dose_warning_gy_per_fraction is not None else "not_configured"
        ))
        criterion_index = self.layer31_high_dose_criterion.findData(criterion_mode)
        self.layer31_high_dose_criterion.setCurrentIndex(max(criterion_index, 0))
        self.layer31_high_dose_threshold.setText(
            "" if config.layer31_lq_high_dose_warning_gy_per_fraction is None
            else str(config.layer31_lq_high_dose_warning_gy_per_fraction)
        )
        self.layer31_high_dose_source.setText(str(criterion.get("source") or ""))
        self._update_layer31_high_dose_controls()
        self.layer31_tumour_scenario.setCurrentText(config.layer31_tumour_scenario or "Not configured")
        self.layer31_normal_scenario.setCurrentText(config.layer31_normal_scenario or "Not configured")
        for tissue, editor, parameters in (
            ("tumour", self.layer31_tumour_kinetics, config.layer31_mlq_tumour_parameters),
            ("normal_cell", self.layer31_normal_kinetics, config.layer31_mlq_normal_parameters),
        ):
            parameter_set_id = str(parameters.get("parameter_set_id") or "")
            known = "zhang_grid_2022" if "zhang-grid-2022" in parameter_set_id else ("custom" if parameters else "not_configured")
            preset_index = editor["kinetic_preset"].findData(known)
            editor["kinetic_preset"].setCurrentIndex(max(preset_index, 0))
            self._update_layer31_model_preset(tissue)
            for key in ("parameter_set_id", "parameter_source", "delta_per_gy", "repair_half_time", "treatment_delivery_time"):
                value = parameters.get(key)
                editor[key].setText("" if value is None else str(value))
            editor["time_unit"].setCurrentText(str(parameters.get("time_unit") or "minutes"))
        schedule = config.layer31_tr_reference_schedule
        self.layer31_tr_enabled.setChecked(bool(schedule))
        self.layer31_tr_fraction_count.setText("" if not schedule.get("fraction_count") else str(schedule["fraction_count"]))
        self.layer31_tr_delivery_time.setText("" if schedule.get("delivery_time") is None else str(schedule["delivery_time"]))
        self.layer31_tr_source.setText(str(schedule.get("source") or ""))
        self.layer31_paired_course_path.setText(config.layer31_paired_course_reference_result_path or "")
        tcp = config.layer31_tcp_parameters
        self.layer31_tcp_density.setText("" if tcp.get("clonogen_density_per_cm3") is None else str(tcp["clonogen_density_per_cm3"]))
        self.layer31_tcp_units.setCurrentText(str(tcp.get("units") or "clonogens/cm3"))
        self.layer31_tcp_source.setText(str(tcp.get("source") or ""))
        self.layer31_tcp_parameter_set.setText(str(tcp.get("parameter_set_id") or ""))
        self.layer31_tcp_repopulation.setChecked(bool(tcp.get("repopulation_enabled")))
        self.layer31_tcp_overall_time.setText("" if tcp.get("overall_treatment_time_days") is None else str(tcp["overall_treatment_time_days"]))
        self.layer31_tcp_kickoff.setText("" if tcp.get("kickoff_time_days") is None else str(tcp["kickoff_time_days"]))
        self.layer31_tcp_doubling.setText("" if tcp.get("potential_doubling_time_days") is None else str(tcp["potential_doubling_time_days"]))
        self.layer31_tcp_sensitivity.setChecked(bool(tcp.get("sensitivity_enabled")))
        self.layer31_tcp_sensitivity_values.setText(",".join(str(item) for item in tcp.get("sensitivity_clonogen_density_values", [])))
        self._update_layer31_tcp_controls()
        self._update_layer31_tr_controls(bool(schedule))
        for role, widget in self.role_widgets.items():
            value = config.structure_roles.get(role, "")
            text = ", ".join(value) if isinstance(value, list) else value
            if isinstance(widget, QLineEdit):
                widget.setText(text)
            else:
                widget.setCurrentText(text)
        self._loading_configuration = False

    def _load_role_options(self) -> None:
        case = self.controller.case
        if not case or not case.selected_objects.get("rtstruct"):
            return
        rtstruct_path = Path(str(case.selected_objects["rtstruct"]))
        if not rtstruct_path.is_file():
            return
        dataset = pydicom.dcmread(str(rtstruct_path), stop_before_pixels=True)
        names = [str(item.ROIName) for item in getattr(dataset, "StructureSetROISequence", [])]
        for role, widget in self.role_widgets.items():
            if isinstance(widget, QComboBox):
                current = widget.currentText()
                widget.clear(); widget.addItem(""); widget.addItems(names); widget.setCurrentText(current)
        current_identity = self.oar_roi_selector.currentData()
        rtstruct_uid = str(getattr(dataset, "SOPInstanceUID", ""))
        self.oar_roi_selector.clear()
        self.oar_roi_selector.addItem("Select an RTSTRUCT ROI…", None)
        self.layer31_roi_selector.clear()
        self.layer31_roi_selector.addItem("Select a rasterised RTSTRUCT ROI…", None)
        for item in getattr(dataset, "StructureSetROISequence", []):
            name = str(item.ROIName)
            identity = {
                "rtstruct_sop_instance_uid": rtstruct_uid,
                "roi_number": int(item.ROINumber),
            }
            self.oar_roi_selector.addItem(
                f"{name}  ·  ROI {identity['roi_number']}",
                {"name": name, "display_name": name, "roi_identity": identity},
            )
            self.layer31_roi_selector.addItem(
                f"{name}  ·  ROI {identity['roi_number']}",
                {"name": name, "display_name": name, "roi_identity": identity},
            )
        self._refresh_layer31_roi_table()
        if isinstance(current_identity, dict):
            current_key = self._oar_identity_key(current_identity.get("roi_identity", current_identity))
            for index in range(self.oar_roi_selector.count()):
                candidate = self.oar_roi_selector.itemData(index)
                if isinstance(candidate, dict) and self._oar_identity_key(candidate.get("roi_identity", {})) == current_key:
                    self.oar_roi_selector.setCurrentIndex(index)
                    break

    @staticmethod
    def _oar_identity_key(identity: dict[str, Any]) -> tuple[str, int]:
        return (
            str(identity.get("rtstruct_sop_instance_uid", "")),
            int(identity.get("roi_number", -1)),
        )

    def _refresh_oar_table(self) -> None:
        classification_labels = {
            "containing_organ": "Containing organ",
            "target_excluded_oar": "Target-excluded OAR",
            "separate_critical_oar": "Separate critical OAR",
            "internal_target_structure": "Internal target structure",
        }
        _set_table(self.oar_table, [[
            item.get("display_name") or item.get("name"),
            item.get("roi_identity", {}).get("roi_number"),
            classification_labels.get(str(item.get("classification")), item.get("classification")),
            "RTSTRUCT UID + ROI number",
        ] for item in self._oar_entries], "No optional OAR geometry structures selected.")

    def _infer_geometry_classification(self, _index: int = -1) -> None:
        selected = self.oar_roi_selector.currentData()
        if not isinstance(selected, dict):
            return
        name = re.sub(r"[^A-Z0-9]+", "", str(selected.get("name", "")).upper())
        classification = "internal_target_structure" if name in {"ALLVERTICES", "ALLVALLEYS", "VTVH", "VTVL"} else None
        if classification:
            index = self.oar_classification_selector.findData(classification)
            if index >= 0:
                self.oar_classification_selector.setCurrentIndex(index)

    @staticmethod
    def _looks_like_oar(name: str) -> bool:
        normalised = re.sub(r"[^A-Z0-9]+", "", name.upper())
        tokens = (
            "HEART", "LUNG", "CORD", "ESOPH", "BOWEL", "KIDNEY", "LIVER", "STOMACH",
            "BLADDER", "RECTUM", "BRAINSTEM", "PAROTID", "OPTIC", "CHESTWALL", "SKIN",
        )
        return any(token in normalised for token in tokens)

    def _prefill_oar_geometry(self, _checked: bool = False, *, eclipse_only: bool = False) -> None:
        case = self.controller.case
        if not case:
            return
        eclipse_records = [
            item for item in case.configuration.eclipse_endpoint_prefill.get("supplied_records", [])
            if item.get("import_status") == "valid"
        ]
        eclipse_names = {
            str(item.get("roi_name") or "")
            for item in eclipse_records
        }
        eclipse_normalised = {re.sub(r"[^A-Z0-9]+", "", name.upper()) for name in eclipse_names}
        eclipse_roi_numbers = {int(item["roi_number"]) for item in eclipse_records if item.get("roi_number") is not None}
        configured_roles = case.configuration.structure_roles.values()
        target_names = {
            str(name) for value in configured_roles for name in (value if isinstance(value, list) else [value])
        }
        existing = {self._oar_identity_key(item.get("roi_identity", {})) for item in self._oar_entries}
        added = 0
        for index in range(1, self.oar_roi_selector.count()):
            candidate = self.oar_roi_selector.itemData(index)
            if not isinstance(candidate, dict):
                continue
            name = str(candidate.get("name") or "")
            number = int(candidate.get("roi_identity", {}).get("roi_number", -1))
            supplied_by_eclipse = (
                re.sub(r"[^A-Z0-9]+", "", name.upper()) in eclipse_normalised or number in eclipse_roi_numbers
            )
            if name in target_names:
                continue
            if eclipse_only and not supplied_by_eclipse:
                continue
            if not eclipse_only and not (self._looks_like_oar(name) or supplied_by_eclipse):
                continue
            key = self._oar_identity_key(candidate.get("roi_identity", {}))
            if key in existing:
                continue
            self._oar_entries.append({
                "name": name, "display_name": name,
                "classification": "separate_critical_oar",
                "roi_identity": dict(candidate["roi_identity"]),
                "selection_source": "eclipse_dvh_reference" if supplied_by_eclipse else "rtstruct_name_prefill",
            })
            existing.add(key); added += 1
        self._refresh_oar_table()
        self.activity.setText(f"{added} GEOMETRY CANDIDATE(S) ADDED")

    def _add_or_update_oar(self) -> None:
        selected = self.oar_roi_selector.currentData()
        if not isinstance(selected, dict) or not selected.get("roi_identity"):
            QMessageBox.critical(self, "ASCEND OAR geometry", "Select an RTSTRUCT ROI.")
            return
        classification = self.oar_classification_selector.currentData()
        entry = {
            "name": str(selected.get("name") or selected.get("display_name")),
            "display_name": str(selected.get("display_name") or selected.get("name")),
            "classification": str(classification),
            "roi_identity": dict(selected["roi_identity"]),
        }
        key = self._oar_identity_key(entry["roi_identity"])
        retained = [
            item for item in self._oar_entries
            if self._oar_identity_key(item.get("roi_identity", {})) != key
        ]
        self._oar_entries = [*retained, entry]
        self._refresh_oar_table()
        self.activity.setText("OAR LIST EDITED")
        self.footer_stage.setText("Save mappings to apply OAR changes")

    def _remove_selected_oar(self) -> None:
        row = self.oar_table.currentRow()
        if row < 0 or row >= len(self._oar_entries):
            QMessageBox.information(self, "ASCEND OAR geometry", "Select an OAR row to remove.")
            return
        del self._oar_entries[row]
        self._refresh_oar_table()
        self.activity.setText("OAR LIST EDITED")
        self.footer_stage.setText("Save mappings to apply OAR changes")

    def _select_oar_table_row(self, row: int, _column: int) -> None:
        if row < 0 or row >= len(self._oar_entries):
            return
        entry = self._oar_entries[row]
        key = self._oar_identity_key(entry.get("roi_identity", {}))
        for index in range(self.oar_roi_selector.count()):
            candidate = self.oar_roi_selector.itemData(index)
            if isinstance(candidate, dict) and self._oar_identity_key(candidate.get("roi_identity", {})) == key:
                self.oar_roi_selector.setCurrentIndex(index)
                break
        classification_index = self.oar_classification_selector.findData(entry.get("classification"))
        if classification_index >= 0:
            self.oar_classification_selector.setCurrentIndex(classification_index)

    @staticmethod
    def _layer31_identity_key(identity: dict[str, Any]) -> tuple[str, int]:
        return str(identity.get("rtstruct_sop_instance_uid", "")), int(identity.get("roi_number", -1))

    def _refresh_layer31_roi_table(self) -> None:
        by_identity: dict[tuple[str, int], str] = {}
        for index in range(self.layer31_roi_selector.count()):
            value = self.layer31_roi_selector.itemData(index)
            if isinstance(value, dict) and value.get("roi_identity"):
                by_identity[self._layer31_identity_key(value["roi_identity"])] = str(value.get("name") or value.get("display_name") or "ROI")
        rows = []
        for item in self._layer31_roi_entries:
            identity = item.get("roi_identity", {})
            rows.append([
                by_identity.get(self._layer31_identity_key(identity), item.get("roi_name") or "Identity-bound ROI"),
                identity.get("roi_number"), item.get("alpha_beta_gy"), item.get("parameter_source_type"),
                item.get("parameter_source"), item.get("parameter_set_version"),
            ])
        _set_table(self.layer31_roi_table, rows, "No tissue parameter assignments configured.")

    def _refresh_layer31_component_table(self) -> None:
        _set_table(self.layer31_component_table, [[
            item.get("component_id"), item.get("fraction_dose_model"),
            "\n".join(item.get("fraction_layer1_result_paths", [])) if item.get("fraction_layer1_result_paths")
            else item.get("layer1_result_path"),
        ] for item in self._layer31_component_entries], "The current validated Layer 1 plan will be used.")

    def _add_layer31_component_source(self) -> None:
        component_id = self.layer31_source_component.currentData()
        if not component_id:
            QMessageBox.information(self, "ASCEND Layer 3.1", "Add treatment components on Case configuration before assigning multiple validated dose sources."); return
        model = str(self.layer31_source_model.currentData())
        case = self.controller.case
        start = str(case.root if case else Path.cwd())
        if model == "explicit_fraction_doses":
            paths, _ = QFileDialog.getOpenFileNames(self, "Select one validated layer1_result.json per delivered fraction", start, "Layer 1 results (layer1_result.json);;JSON files (*.json)")
            if not paths:
                return
            record = {"component_id": str(component_id), "fraction_dose_model": model, "fraction_layer1_result_paths": paths, "fraction_count": len(paths)}
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select validated cumulative layer1_result.json", start, "Layer 1 result (layer1_result.json);;JSON files (*.json)")
            if not path:
                return
            record = {"component_id": str(component_id), "fraction_dose_model": model, "layer1_result_path": path}
        self._layer31_component_entries = [item for item in self._layer31_component_entries if str(item.get("component_id")) != str(component_id)] + [record]
        self._refresh_layer31_component_table()

    def _remove_layer31_component_source(self) -> None:
        row = self.layer31_component_table.currentRow()
        if row < 0 or row >= len(self._layer31_component_entries):
            QMessageBox.information(self, "ASCEND Layer 3.1", "Select a component-source row to remove."); return
        del self._layer31_component_entries[row]
        self._refresh_layer31_component_table()

    def _add_layer31_roi_assignment(self) -> None:
        selected = self.layer31_roi_selector.currentData()
        if not isinstance(selected, dict) or not selected.get("roi_identity"):
            QMessageBox.critical(self, "ASCEND Layer 3.1", "Select a rasterised RTSTRUCT ROI.")
            return
        try:
            alpha_beta = self._number(self.layer31_alpha_beta.text())
            if alpha_beta is None or alpha_beta <= 0:
                raise ValueError("Alpha/beta must be greater than zero.")
            source = self.layer31_parameter_source.text().strip()
            set_id = self.layer31_parameter_set.text().strip()
            if not source or not set_id:
                raise ValueError("Parameter source and parameter-set ID are required.")
        except ValueError as exc:
            QMessageBox.critical(self, "ASCEND Layer 3.1", str(exc)); return
        record = {
            "roi_identity": dict(selected["roi_identity"]), "roi_name": selected.get("name"),
            "alpha_beta_gy": alpha_beta, "parameter_source": source,
            "parameter_source_type": str(self.layer31_parameter_source_type.currentData()),
            "parameter_set_version": set_id, "assignment_method": "qt_identity_bound",
        }
        key = self._layer31_identity_key(record["roi_identity"])
        self._layer31_roi_entries = [
            item for item in self._layer31_roi_entries if self._layer31_identity_key(item.get("roi_identity", {})) != key
        ] + [record]
        self._refresh_layer31_roi_table()
        self.footer_stage.setText("Layer 3.1 tissue assignment staged; save configuration to apply")

    def _remove_layer31_roi_assignment(self) -> None:
        row = self.layer31_roi_table.currentRow()
        if row < 0 or row >= len(self._layer31_roi_entries):
            QMessageBox.information(self, "ASCEND Layer 3.1", "Select an assignment row to remove."); return
        del self._layer31_roi_entries[row]
        self._refresh_layer31_roi_table()

    def _layer31_kinetic_parameters(self, editor: dict[str, Any], scenario: str, tissue: str) -> dict[str, Any]:
        if scenario == "Not configured":
            return {}
        values = {
            "parameter_set_id": editor["parameter_set_id"].text().strip(),
            "parameter_source": editor["parameter_source"].text().strip(),
            "model_source": "Guerrero–Li modified linear-quadratic model",
            "delta_per_gy": self._number(editor["delta_per_gy"].text()),
            "repair_half_time": self._number(editor["repair_half_time"].text()),
            "treatment_delivery_time": self._number(editor["treatment_delivery_time"].text()),
            "time_unit": editor["time_unit"].currentText(),
            "delivery_time_source": "explicit_case_configuration",
            "tissue_identity": tissue,
        }
        missing = [key for key in ("parameter_set_id", "parameter_source", "delta_per_gy", "repair_half_time", "treatment_delivery_time") if values[key] in (None, "")]
        if missing:
            editor["status"].setText(
                f"INCOMPLETE · calculation will be blocked until supplied: {', '.join(missing)}. Configuration can still be saved."
            )
        return values

    def _run_layer31(self) -> None:
        if self._save_configuration(silent=True):
            self._work(self.controller.run_layer31)

    def _build_layer31_visualization(self) -> None:
        case = self.controller.case
        if not case or not case.layer3_1.result:
            QMessageBox.critical(self, "ASCEND Layer 3.1", "Run Layer 3.1 before building the biological field viewer."); return
        self.layer31_viewer_status.setText("Loading hash-verified biological fields and generating display-only anatomical meshes…")
        from ascend.gui.layer31_viewer import prepare_layer31_viewer_data
        self._work(lambda: prepare_layer31_viewer_data(case), self._show_layer31_visualization)

    def _show_layer31_visualization(self, data: Any) -> None:
        if self.layer31_viewer is None:
            from ascend.gui.layer31_viewer import Layer31Viewer
            self.layer31_viewer = Layer31Viewer()
            self.layer31_viewer.scenarioRequested.connect(self._run_layer31_viewer_scenario)
            self.layer31_viewer_layout.addWidget(self.layer31_viewer, 1)
        self.layer31_viewer.set_data(data)
        self.layer31_viewer.setEnabled(True)
        self.layer31_viewer_run_id = self.controller.case.layer3_1.run_id if self.controller.case else None
        self.layer31_viewer_status.setText("Showing authoritative stored fields. Surface smoothing changes display geometry only and cannot alter biological results.")
        self.layer31_tabs.setCurrentIndex(1)

    def _run_layer31_viewer_scenario(self, scenario: str) -> None:
        """Route a viewer scenario change through configuration and services."""
        if scenario not in TUMOUR_SCENARIOS:
            return
        self.layer31_tumour_scenario.setCurrentText(scenario)
        self._update_layer31_model_preset("tumour")
        if self._save_configuration(silent=True):
            self.layer31_viewer_status.setText(f"Recalculating stored Layer 3.1 fields for {scenario}…")
            self._work(self.controller.run_layer31, lambda _value: self._build_layer31_visualization())

    def _export_layer31(self) -> None:
        case = self.controller.case
        if not case or not case.layer3_1.result:
            QMessageBox.information(self, "ASCEND Layer 3.1", "No current Layer 3.1 result is available to export."); return
        destination = QFileDialog.getExistingDirectory(self, "Select Layer 3.1 export directory", str(case.root / "exports"))
        if destination:
            self._work(lambda: self.controller.export_layer31(destination), self._show_exports)

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
        self.layer22_viewer_status.setText(
            f"Rendered from Layer 1 validated masks and native RTDOSE. Vertex source: {data.vertex_source}."
        )
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

    def _tps_and_dose_labels(self, case: ASCENDCase) -> tuple[str, str]:
        plan_path = case.selected_objects.get("rtplan")
        dose_path = case.selected_objects.get("rtdose")
        plan = next((item for item in case.dicom_objects.get("RTPLAN", []) if item.get("path") == plan_path), {})
        dose = next((item for item in case.dicom_objects.get("RTDOSE", []) if item.get("path") == dose_path), {})
        tps = plan.get("manufacturer") or plan.get("plan_label") or "not recorded"
        dose_label = dose.get("dose_summation_type") or "selected RTDOSE"
        return str(tps), str(dose_label)

    def _set_navigation_status(self, index: int, status: Any) -> None:
        item = self.navigation_items[index]
        state = canonical_state(status)
        compact = {
            "NOT IMPLEMENTED": "N/I",
            "NOT APPLICABLE": "N/A",
            "OUTSIDE SCOPE": "SCOPE",
            "PROVISIONAL": "PROV.",
        }.get(state, state)
        item.setText(f"{self.navigation_labels[index]}   [{compact}]")
        item.setToolTip(f"{self.navigation_labels[index]} — {state}")
        colours = {
            "PASS": QColor("#18704d"),
            "WARN": QColor("#9a6500"),
            "PROVISIONAL": QColor("#6744a4"),
            "BLOCKED": QColor("#aa2e2e"),
            "INVALID": QColor("#aa2e2e"),
            "OUTSIDE SCOPE": QColor("#596979"),
        }
        item.setForeground(colours.get(state, self.palette().color(QPalette.Text)))

    def _refresh_navigation(self, case: ASCENDCase | None) -> None:
        if case is None:
            for index in self.navigation_items:
                self._set_navigation_status(index, "not_run")
            return
        has_bindings = bool(case.configuration.structure_bindings or case.configuration.structure_roles)
        statuses = {
            0: "pass",
            1: "pass",
            2: "pass" if has_bindings else "warn",
            3: case.layer1_status,
            4: case.layer2_1.calculation_status,
            5: case.layer2_2.calculation_status,
            6: case.layer3_1.calculation_status,
            7: case.layer3_2.calculation_status if case.configuration.layer32_enabled else "not_applicable",
            8: "provisional" if case.layer2_1.result or case.layer2_2.result else "not_run",
            9: "pass" if case.layer2_1.result or case.layer2_2.result or case.layer3_1.result or case.layer3_2.result else "not_run",
        }
        for index, status in statuses.items():
            self._set_navigation_status(index, status)

    def _refresh_mapping_table(self, case: ASCENDCase) -> None:
        rows: list[list[Any]] = []
        requirements = {
            "GTV": "Required",
            "T_L": "Required",
            "VTV_H": "Required",
            "VTV_L": "Required",
            "VTV_H_individual": "Optional",
        }
        for role, requirement in requirements.items():
            binding = case.configuration.structure_bindings.get(role)
            legacy = case.configuration.structure_roles.get(role)
            identities = binding if isinstance(binding, list) else ([binding] if binding else [])
            names = [str(item.get("display_name") or "Unnamed ROI") for item in identities]
            numbers = [str(item.get("roi_number", "—")) for item in identities]
            if identities:
                state = "Bound by ROI identity"
            elif legacy:
                legacy_values = legacy if isinstance(legacy, list) else [legacy]
                names = [str(item) for item in legacy_values]
                numbers = ["—"] * len(names)
                state = "Legacy name; resolved when saved"
            else:
                state = "Not configured" if requirement == "Optional" else "Required mapping missing"
            rows.append([role, requirement, ", ".join(names) or "—", ", ".join(numbers) or "—", state])
        _set_table(self.mapping_table, rows)

    @staticmethod
    def _json_or_state(value: Any, empty_text: str) -> str:
        if value in (None, {}, []):
            return empty_text
        return json.dumps(value, indent=2)

    def refresh(self) -> None:
        """Handle refresh for the enclosing ASCEND workflow."""
        case = self.controller.case
        if not case:
            self.header_case.setText("No case open")
            self.header_status.setText("TPS —  ·  Dose —")
            for pill in (self.header_layer1, self.header_layer21, self.header_layer22, self.header_interpretation):
                pill.set_status("not_run")
            self.sidebar_case.setText("No case open\nImport or open an ASCEND case")
            self.footer_run.setText("Run —")
            self._refresh_navigation(None)
            return
        interpretations = (case.layer2_1.interpretation_status, case.layer2_2.interpretation_status)
        interpretation = "protocol_interpretable" if all(item == "protocol_interpretable" for item in interpretations) else ("provisional" if "provisional" in interpretations else "not_interpretable")
        tps, dose = self._tps_and_dose_labels(case)
        self.header_case.setText(case.case_id)
        self.header_status.setText(f"TPS {tps}  ·  Dose {dose}")
        self.header_layer1.set_status(case.layer1_status)
        self.header_layer21.set_status(case.layer2_1.calculation_status)
        self.header_layer22.set_status(case.layer2_2.calculation_status)
        self.header_interpretation.set_status(interpretation)
        self.sidebar_case.setText(
            f"{case.case_id}\nLayer 1 {canonical_state(case.layer1_status)}\n"
            f"Chain {case.selected_chain_id or 'selection required'}"
        )
        latest_run = case.layer3_2.run_id or case.layer3_1.run_id or case.layer2_2.run_id or case.layer2_1.run_id or case.layer1.run_id
        self.footer_run.setText(f"Run {latest_run or '—'}")
        self._refresh_navigation(case)
        counts = {key: len(value) for key, value in case.dicom_objects.items()}
        self.import_summary.setPlainText(json.dumps({"case_id": case.case_id, "detected_objects": counts, "selected": case.selected_objects}, indent=2))
        self.chain_select.clear()
        for chain in case.dicom_chains:
            self.chain_select.addItem(
                f"{chain.get('display', {}).get('plan_label', '') or 'Unlabelled plan'}  ·  "
                f"Dose {chain.get('display', {}).get('dose_summation_type', '') or '—'}  ·  "
                f"{chain['validity_status']}  ·  {chain['chain_id']}",
                chain["chain_id"],
            )
        if case.selected_chain_id:
            index = self.chain_select.findData(case.selected_chain_id)
            if index >= 0:
                self.chain_select.setCurrentIndex(index)
        selected_chain = next((item for item in case.dicom_chains if item.get("chain_id") == case.selected_chain_id), None)
        if selected_chain:
            self.chain_detail.setText(
                f"Selected chain is {selected_chain.get('validity_status')}. It binds RTDOSE, RTPLAN, RTSTRUCT, "
                f"and {selected_chain.get('display', {}).get('image_count', 0)} planning image(s). "
                f"Unresolved references: {', '.join(selected_chain.get('unresolved_references', [])) or 'none'}."
            )
        elif len(case.dicom_chains) > 1:
            self.chain_detail.setText(
                f"Selection required: {len(case.dicom_chains)} candidate treatment chains were found. "
                "Layer 1 remains blocked until one chain is explicitly selected."
            )
        l1 = case.layer1.result or {}
        eligibility = l1.get("eligibility", {})
        self.layer1_status_pill.set_status(case.layer1_status)
        self.layer1_card.setText(
            f"Calculation: {canonical_state(case.layer1.calculation_status)}  ·  "
            f"Layer 2 eligible: {'yes' if eligibility.get('layer_2_eligible', False) else 'no'}"
        )
        findings = list(l1.get("findings", []))
        finding_messages = [f"{item.get('check', 'finding')}: {item.get('detail', '')}" for item in findings]
        blocked = canonical_state(case.layer1_status) == "BLOCKED" or any(
            str(item.get("level", "")).upper() in {"BLOCK", "BLOCKED"} or bool(item.get("blocks"))
            for item in findings
        )
        self.layer1_banner.set_messages(finding_messages, blocked=blocked)
        _set_table(self.layer1_findings, [[item.get("level"), item.get("check"), item.get("detail"), item.get("blocks", "")]
                                                  for item in findings], "Run Layer 1 to populate validation findings.")
        _set_table(self.layer1_eclipse_audit, [[
            item.get("original_structure"), item.get("ascend_role"), item.get("validated_structure"),
            item.get("metric"), item.get("eclipse_value"), item.get("ascend_value"),
            item.get("difference"), item.get("unit"), item.get("status"),
        ] for item in l1.get("eclipse_dvh_audit", [])], "No Eclipse comparison records are available.")
        self.layer1_eclipse_import.setPlainText(self._json_or_state(
            l1.get("eclipse_dvh_import"), "No Eclipse DVH reference has been imported."
        ))
        self._refresh_mapping_table(case)
        self._refresh_layer21(case)
        self._refresh_layer22(case)
        self._refresh_layer31(case)
        self._refresh_layer32(case)
        self.review_text.setPlainText(json.dumps(case.to_dict(include_results=False), indent=2))
        self.export_path.setText(str(case.root / "exports"))

    def _refresh_layer21(self, case: ASCENDCase) -> None:
        record = case.layer2_1
        result = record.result or {}
        self.layer21_status_pill.set_status(record.calculation_status)
        self.layer21_interpretation_pill.set_status(record.interpretation_status)
        self.layer21_card.setText(f"Run {record.run_id or '—'}")
        warnings = list(result.get("warnings", record.warnings))
        if record.error:
            warnings.insert(0, record.error)
        blocked = canonical_state(record.calculation_status) in {"BLOCKED", "INVALID"}
        self.layer21_warnings.set_messages(warnings, blocked=blocked)
        rows = []
        metrics = result.get("harmonised_metrics", [])
        for card in self.metric_cards.values():
            card.set_metric(None)
        for item in metrics:
            value = item.get("value")
            metric_id = str(item.get("metric_id", ""))
            if metric_id in self.metric_cards:
                self.metric_cards[metric_id].set_metric(item)
            rows.append([METRIC_LABELS.get(metric_id, metric_id), "" if value is None else value, item.get("units"), item.get("applicability"), ", ".join(item.get("warnings", []))])
        _set_table(self.metric_table, rows, "Run Layer 2.1 to populate harmonised metrics.")
        stored_supporting = result.get("supporting_outputs") or {}
        categories = [category for category, checkbox in self.supporting_output_checks.items() if checkbox.isChecked()]
        supporting = selected_supporting_outputs(
            stored_supporting, self.supporting_outputs_enabled.isChecked(), categories,
        )
        self._current_supporting_outputs = supporting
        self.export_supporting_json_button.setEnabled(bool(supporting))
        _set_table(
            self.layer21_support,
            supporting_output_rows(supporting),
            "No stored supporting output is available for this run.",
        )
        vertex_analysis = stored_supporting.get("vertex_analysis", {}) if "per_vertex" in categories and self.supporting_outputs_enabled.isChecked() else {}
        vertex_records = stored_supporting.get(
            "per_vertex_qa",
            result.get("per_vertex_quality_control", result.get("per_vertex_qa", [])),
        ) or [] if "per_vertex" in categories and self.supporting_outputs_enabled.isChecked() else []
        vertex_records = normalise_vertex_records(vertex_records)
        source = vertex_analysis.get("source", "not recorded")
        self.layer21_vertex_summary.setText(
            f"{len(vertex_records)} stored record(s)  ·  Source: {source}  ·  "
            f"Status: {vertex_analysis.get('status', 'not calculated')}"
        )
        _set_table(self.layer21_vertex_table, [[
            item.get("vertex_id"), item.get("v95_rxh_pct"), item.get("v95_rxh_applicability", item.get("applicability")),
            item.get("dmean_gy"), item.get("d95_gy"), item.get("dmax_gy"), item.get("volume_cc"),
        ] for item in vertex_records], "No per-vertex QA records were stored for this run.")
        self.layer21_vertex.setPlainText(self._json_or_state(
            vertex_analysis, "Per-vertex analysis metadata is not available."
        ))
        oar = result.get("oar_vertex_geometry", stored_supporting.get("oar_vertex_geometry", {})) if "oar_geometry" in categories and self.supporting_outputs_enabled.isChecked() else {}
        self.layer21_oar_status.setText(
            f"Status: {oar.get('status', 'not selected')}  ·  "
            f"{oar.get('scope', 'No optional OAR or internal-target geometry result is available.')}"
        )
        _set_table(self.layer21_oar_table, [[
            item.get("oar_name"), item.get("classification"), item.get("oar_volume_cc"),
            item.get("aggregate_vtvh_minimum_surface_distance_mm"),
            item.get("aggregate_vtvh_spatial_relationship"), item.get("overlap_volume_cc"),
            item.get("overlap_percentage_of_oar"), item.get("nearest_vertex_id"),
            item.get("nearest_vertex_distance_mm"),
            "; ".join(finding.get("code", "") for finding in item.get("geometry_audit", {}).get("findings", []))
            or item.get("status") or item.get("reason"),
        ] for item in oar.get("records", [])], "No selected OAR or internal-target geometry records were calculated.")
        _set_table(self.layer21_oar_vertex_table, [[
            item.get("oar_name"), vertex.get("vertex_id"), vertex.get("minimum_surface_distance_mm"),
            vertex.get("overlap_volume_cc"), vertex.get("spatial_relationship"), vertex.get("zero_distance_reason"),
        ] for item in oar.get("records", []) for vertex in item.get("per_vertex_geometry", [])],
            "No per-vertex OAR geometry audit records were calculated.")
        provenance = result.get("provenance") if "integrity" in categories and self.supporting_outputs_enabled.isChecked() else None
        self.layer21_provenance.setPlainText(self._json_or_state(provenance, "Provenance output is not selected."))

    def _refresh_layer22(self, case: ASCENDCase) -> None:
        record = case.layer2_2
        result = record.result or {}
        self.layer22_status_pill.set_status(record.calculation_status)
        self.layer22_interpretation_pill.set_status(record.interpretation_status)
        self.layer22_card.setText(f"Run {record.run_id or '—'}")
        warnings = list(result.get("warnings", record.warnings))
        if record.error:
            warnings.insert(0, record.error)
        vertex_source = result.get("vertex_source") or result.get("frozen_definitions", {}).get("vertex_source")
        blocked = canonical_state(record.calculation_status) in {"BLOCKED", "INVALID"}
        self.layer22_warnings.set_messages(warnings, blocked=blocked)
        self.graph_canvas.set_result(result or None)
        summary = result.get("graph_summary") or {}
        plan_ipvdr = result.get("plan_ipvdr") or {}
        median = plan_ipvdr.get("primary_median")
        median_text = f"{float(median):.3f}" if isinstance(median, (int, float)) else "—"
        self.graph_result_summary.setText(
            f"{summary.get('number_of_nodes', 0)} nodes  ·  {summary.get('number_of_edges', 0)} edges  ·  "
            f"Median iPVDR {median_text}  ·  {vertex_source or 'vertex source not recorded'}"
        )
        pass_meaning = (
            "Computational PASS: required valid edges were available and no framework warning was raised. "
            "It is not a clinical PVDR acceptance threshold or treatment-plan approval."
        )
        _set_table(self.graph_summary, [
            ["Calculation state", canonical_state(record.calculation_status), pass_meaning],
            ["Interpretation state", canonical_state(record.interpretation_status), "Layer 2.2 remains provisional; graph metrics require expert interpretation."],
            ["Vertex source", vertex_source, "Explicit RTSTRUCT vertices are preferred; connected components generate a warning."],
            ["Nodes", summary.get("number_of_nodes"), "Validated vertex masks represented as graph nodes."],
            ["Edges", summary.get("number_of_edges"), "Deterministic nearest-neighbour connections."],
            ["Connected components", summary.get("number_of_components"), "PASS requires one connected graph under the current warning policy."],
            ["Valid edges", summary.get("valid_edges"), "Edges meeting the frozen midpoint-sphere support rule."],
            ["Excluded edges", summary.get("excluded_edges", len(result.get("excluded_edges", []))), "Excluded edges are retained for audit and raise a warning."],
            ["Median edge iPVDR", plan_ipvdr.get("primary_median"), "Descriptive median of valid edge-local iPVDR values; no clinical cutoff is applied."],
            ["Warnings", ", ".join(warnings) or "None", "Any warning changes calculation display from PASS to WARN."],
        ] if result else [], "No Layer 2.2 graph result is available.")
        _set_table(self.graph_nodes, [[item.get("node"), *(item.get("centroid_lps_mm") or [None, None, None]), item.get("peak_d50_gy")] for item in result.get("nodes", [])], "No graph nodes are available.")
        _set_table(self.graph_edges, [[item.get("edge_id"), " — ".join(item.get("nodes", [])), item.get("length_mm"), item.get("edge_local_valley_d50_gy"), item.get("ipvdr"), item.get("edge_status")] for item in result.get("edges", [])], "No graph edges are available.")
        self.graph_provenance.setPlainText(self._json_or_state(
            result.get("provenance"), "No Layer 2.2 provenance is available."
        ))
        if self.layer22_viewer_run_id and self.layer22_viewer_run_id != record.run_id:
            self.layer22_viewer_status.setText("STALE — Layer 2.2 changed. Rebuild the 3D viewer before interpretation.")
            if self.layer22_viewer is not None:
                self.layer22_viewer.setEnabled(False)

    def _refresh_layer31(self, case: ASCENDCase) -> None:
        record = case.layer3_1
        result = record.result or {}
        self.layer31_status_pill.set_status(record.calculation_status)
        self.layer31_interpretation_pill.set_status(record.interpretation_status)
        history = result.get("fraction_history") or {}
        self.layer31_status_text.setText(
            f"Run {record.run_id or '—'}  ·  {history.get('number_of_biological_fraction_events', 0)} biological fraction event(s)  ·  "
            f"{len(result.get('roi_results', []))} spatial ROI result(s)"
            + (f"  ·  {record.error}" if record.error else "")
        )
        gate_rows: list[list[Any]] = []
        for gate in history.get("gate_results", []):
            gate_rows.append([gate.get("gate_id"), gate.get("status"), gate.get("reason_code") or gate.get("detail") or gate.get("evidence")])
        branch_specs = (
            ("3.1A Spatial BED/EQD2", result.get("layer3_1a_conventional_lq") or {}),
            ("3.1B Tumour survival/EUD", result.get("layer3_1b_high_dose_sfrt_response") or {}),
            ("3.1C Therapeutic ratio", result.get("layer3_1c_modelled_therapeutic_ratio") or {}),
            ("3.1D Spatial MLQ-Poisson TCP", result.get("layer3_1d_tumour_control_probability") or {}),
        )
        for label, branch in branch_specs:
            gate_rows.append([label, branch.get("status") or branch.get("calculation_status") or "NOT RUN", branch.get("reason") or ", ".join(branch.get("warnings", [])) or "No blocking reason recorded"])
        _set_table(self.layer31_gate_table, gate_rows, "Run Layer 3.1 to evaluate prerequisite gates.")
        _set_table(self.layer31_history_table, [[
            event.get("temporal_order"), event.get("event_id"), ", ".join(event.get("physical_components", [])),
            event.get("biological_fraction_index"), event.get("geometry_reference"),
            f"{event.get('delivery_time')} {event.get('delivery_time_unit') or ''}" if event.get("delivery_time") is not None else "Parameter-set fallback required for MLQ",
            "Dose: " + ", ".join(event.get("source_dose_identifiers", [])) + " | Plan: " + ", ".join(event.get("source_plan_identifiers", [])),
        ] for event in history.get("events", [])], "No reconstructed fraction history is stored.")

        branch_a = result.get("layer3_1a_conventional_lq") or {}
        warning = branch_a.get("high_dose_warning") or {}
        warning_by_identity = {
            self._layer31_identity_key(item.get("roi_identity", {})): item
            for item in warning.get("roi_summary", [])
        }
        a_rows = []
        for item in result.get("roi_results", []):
            assignment = item.get("assignment", {}); metrics = item.get("metrics", {})
            flag = warning_by_identity.get(self._layer31_identity_key(assignment.get("roi_identity", {})), {})
            a_rows.append([
                assignment.get("roi_name"), assignment.get("alpha_beta_gy"), metrics.get("bed_mean"), metrics.get("bed_d95"),
                metrics.get("bed_d50"), metrics.get("eqd2_mean"), metrics.get("eqd2_d95"), flag.get("flagged_volume_percent"),
            ])
        _set_table(self.layer31a_table, a_rows, "No 3.1A ROI results are stored.")
        a_messages = list(branch_a.get("warnings", []))
        if warning.get("configured"):
            a_messages.insert(0, warning.get("explanation") or warning.get("message"))
        if branch_a.get("reason"):
            a_messages.insert(0, str(branch_a["reason"]))
        self.layer31a_warning.set_messages(a_messages, blocked=str(branch_a.get("status") or "").upper() == "BLOCKED")

        branch_b = result.get("layer3_1b_high_dose_sfrt_response") or {}
        _set_table(self.layer31b_summary, [
            ["Tumour scenario", branch_b.get("scenario_id"), "Sensitivity scenario", branch_b.get("scenario_scope")],
            ["Mean direct surviving fraction", branch_b.get("mean_tumour_survival_fraction"), "dimensionless", "Model-derived mean; not TCP"],
            ["Equivalent log-survival effect Kᵀ,eq", branch_b.get("equivalent_log_survival_effect"), "dimensionless", branch_b.get("equivalent_log_survival_effect_definition")],
            ["Survival-equivalent EUD", branch_b.get("tumour_eud_gy"), "Gy", branch_b.get("eud_applicability") or branch_b.get("reason")],
            ["Reference schedule", (branch_b.get("reference_schedule") or {}).get("schedule_type"), branch_b.get("applicability_status"), branch_b.get("reference_schedule")],
            ["Calculation state", branch_b.get("status") or branch_b.get("calculation_status"), branch_b.get("interpretation_status"), branch_b.get("reason") or ", ".join(branch_b.get("warnings", []))],
        ] if branch_b else [], "No 3.1B tumour response result is stored.")
        comparison = result.get("layer3_1b_paired_course_comparison") or {}
        differences = comparison.get("comparison") or {}
        _set_table(self.layer31b_comparison, [
            ["Comparison state", comparison.get("status"), comparison.get("applicability_status"), comparison.get("reason") or comparison.get("comparison_scope")],
            ["SF difference: LRT+cERT − LRT", differences.get("sf_difference_lrt_plus_cert_minus_lrt"), "dimensionless", comparison.get("arms")],
            ["Equivalent-effect difference", differences.get("equivalent_log_survival_effect_difference"), "dimensionless", "Positive means greater modelled log-survival effect for LRT+cERT"],
            ["EUD difference: LRT+cERT − LRT", differences.get("eud_difference_gy_lrt_plus_cert_minus_lrt"), "Gy", "Research comparison; not a clinical outcome"],
        ] if comparison else [], "No paired-course comparison is configured.")
        regional = (branch_b.get("regional_survival") or {}).get("records", [])
        region_names = {"H": "High-dose vertices", "V": "Validated valley", "O": "Remaining tumour"}
        _set_table(self.layer31b_regional, [[
            region_names.get(item.get("region_id"), item.get("region_id")), item.get("voxel_count"),
            item.get("tumour_volume_fraction"), item.get("mean_surviving_fraction"), item.get("survivor_contribution_fraction"),
        ] for item in regional], "No regional survival decomposition is stored.")
        reconciliation = (branch_b.get("regional_survival") or {}).get("high_dose_fraction_reconciliation") or {}
        _set_table(self.layer31b_hf_reconciliation, [
            ["Layer 2.1 reported HF", reconciliation.get("layer2_1_reported_value_pct"), reconciliation.get("layer2_1_reported_basis"), None],
            ["Layer 2.1 dose-sampled HF", reconciliation.get("layer2_1_dose_sampled_value_pct"), "RTDOSE-sampled mask voxels", reconciliation.get("dose_sampled_difference_percentage_points_layer31b_minus_layer21")],
            ["Layer 3.1B regional fH", reconciliation.get("layer3_1b_value_pct"), reconciliation.get("layer3_1b_basis"), reconciliation.get("reported_difference_percentage_points_layer31b_minus_layer21")],
        ] if reconciliation else [], "No high-dose fraction reconciliation is available.")

        branch_c = result.get("layer3_1c_modelled_therapeutic_ratio") or {}
        _set_table(self.layer31c_summary, [
            ["Modelled therapeutic ratio", branch_c.get("modelled_therapeutic_ratio"), branch_c.get("applicability_status"), branch_c.get("reference_schedule") or branch_c.get("reason")],
            ["Actual heterogeneous normal-cell SF", branch_c.get("normal_mean_survival_lrt"), branch_c.get("applicability_status"), "Research comparator only"],
            ["Reference normal-cell SF", branch_c.get("normal_survival_at_tumour_eud"), branch_c.get("applicability_status"), "Uniform tumour-isoeffective schedule"],
        ] if branch_c else [], "No 3.1C result is stored.")
        matrix = result.get("layer3_1c_sensitivity_scenario_matrix") or {}
        _set_table(self.layer31c_matrix, [[
            item.get("tumour_scenario"), item.get("normal_scenario"), item.get("therapeutic_ratio"),
            item.get("tumour_eud_gy"), item.get("normal_mean_survival_actual"), item.get("normal_survival_reference"),
            item.get("applicability_status") or item.get("reason"),
        ] for item in matrix.get("records", [])], "The C1–C3 × N1–N3 scenario matrix has not been calculated.")
        branch_d = result.get("layer3_1d_tumour_control_probability") or {}
        endpoints = branch_d.get("endpoints") or {}
        radiation = endpoints.get("radiation_only") or {}
        corrected = endpoints.get("repopulation_corrected") or {}
        source_context = branch_d.get("source_context") or {}
        spatial = endpoints.get("spatial_decomposition") or {}
        valley_record = next((item for item in spatial.get("records", []) if item.get("region_id") == "VALLEY"), {})
        active = corrected if branch_d.get("active_tcp_endpoint") == "TCP_MLQ_POISSON_REPOPULATION_CORRECTED" else radiation
        _set_table(self.layer31d_summary, [
            [branch_d.get("active_tcp_endpoint") or "Qualified TCP", active.get("tcp"), "probability", "Poisson probability of zero expected surviving clonogens under the configured direct-kill model"],
            ["Active TCP percentage", (100.0 * active["tcp"]) if active.get("tcp") is not None else None, "%", branch_d.get("interpretation_status")],
            ["Expected residual clonogens", active.get("expected_surviving_clonogens"), "clonogens", "Primary endpoint retained when TCP saturates"],
            ["Initial clonogens", endpoints.get("initial_clonogens"), "clonogens", "Density multiplied by validated physical tumour volume"],
            ["Mean tumour MLQ survival", source_context.get("mean_tumour_survival_fraction"), "dimensionless", "Consumed from Layer 3.1B"],
            ["Tumour EUD", source_context.get("tumour_eud_gy"), "Gy", "Consumed from Layer 3.1B"],
            ["Valley residual fraction", valley_record.get("residual_fraction"), "fraction", spatial.get("status")],
        ] if branch_d else [], "TCP unavailable: configure parameters and provide a valid Layer 3.1B tumour survival result.")
        _set_table(self.layer31d_comparison, [
            ["TCP_MLQ_POISSON_RADIATION_ONLY", radiation.get("tcp"), radiation.get("ln_tcp"), radiation.get("expected_surviving_clonogens")],
            ["TCP_MLQ_POISSON_REPOPULATION_CORRECTED", corrected.get("tcp"), corrected.get("ln_tcp"), corrected.get("expected_surviving_clonogens")],
        ] if endpoints else [], "No qualified TCP comparison is available.")
        _set_table(self.layer31d_spatial, [[
            item.get("region_id"), item.get("volume_cm3"), item.get("mean_radiation_survival_fraction"),
            item.get("expected_residual_clonogens"), item.get("residual_fraction"), item.get("p0"),
        ] for item in spatial.get("records", [])], "Whole-tumour TCP may be valid; vertex/valley decomposition is unavailable.")
        sensitivity = branch_d.get("sensitivity_analysis") or {}
        _set_table(self.layer31d_sensitivity, [[
            item.get("parameter"), item.get("value"), item.get("tcp_radiation_only"), item.get("expected_surviving_clonogens"),
        ] for item in sensitivity.get("records", [])], "Layer 3.1D sensitivity is disabled or unavailable.")
        d_messages = list(branch_d.get("warnings", []))
        if branch_d.get("reason"): d_messages.insert(0, str(branch_d["reason"]))
        self.layer31d_warning.set_messages(d_messages, blocked=str(branch_d.get("status") or "").upper() == "BLOCKED")
        self.layer31d_provenance.setPlainText(json.dumps({
            "model": branch_d.get("tcp_model"), "clonogen_model": branch_d.get("clonogen_model"),
            "repopulation": branch_d.get("repopulation"), "validation_status": branch_d.get("validation_status"),
            "assumptions": branch_d.get("assumptions"), "gates": branch_d.get("gate_results"),
            "provenance": branch_d.get("provenance"),
        }, indent=2, default=str) if branch_d else "No Layer 3.1D provenance is stored.")
        self.layer31_provenance.setPlainText(json.dumps({
            "scientific_position": result.get("scientific_position"), "scope_exclusions": result.get("scope_exclusions"),
            "fraction_history": history, "treatment_context": result.get("treatment_context"),
            "model_3_1a": branch_a, "model_3_1b": branch_b, "model_3_1c": branch_c,
            "paired_course_comparison": comparison,
            "model_3_1d": branch_d,
            "visualisation": result.get("visualisation"), "provenance": result.get("provenance"),
        }, indent=2, default=str) if result else "No Layer 3.1 provenance is stored.")
        if self.layer31_viewer_run_id and self.layer31_viewer_run_id != record.run_id:
            self.layer31_viewer_status.setText("STALE — Layer 3.1 changed. Rebuild the biological viewer.")
            if self.layer31_viewer is not None:
                self.layer31_viewer.setEnabled(False)

    def _refresh_layer32(self, case: ASCENDCase) -> None:
        """Present current Layer 3.2 records without recalculating any field or endpoint."""
        record = case.layer3_2
        enabled = case.configuration.layer32_enabled
        self.layer32_enabled.blockSignals(True)
        self.layer32_enabled.setChecked(enabled)
        self.layer32_enabled.blockSignals(False)
        self._update_layer32_enabled_controls(enabled)
        if not enabled:
            self.layer32_status_pill.set_status("not_applicable")
            self.layer32_interpretation_pill.set_status("not_applicable")
            self.layer32_status_text.setText(
                "NOT ASSESSED — DISABLED. Layer 3.2 is excluded from calculation and interpretation for this case."
            )
            self.layer32_warnings.badge.set_status("not_applicable")
            self.layer32_warnings.detail.setText(
                "Enable Layer 3.2 to include the optional non-local research model. Layers 1, 2.1, 2.2, and 3.1 are unaffected."
            )
            disabled_text = "Layer 3.2 is disabled; no result is being considered."
            for table in (
                self.layer32_parameter_table, self.layer32_scenario_table,
                self.layer32_graph_summary, self.layer32_edge_table, self.layer32_gtv_table,
                self.layer32_shell_table, self.layer32_oar_table, self.layer32_assay_table,
                self.layer32_regional_table,
            ):
                _set_table(table, [], disabled_text)
            _set_table(self.layer32_configuration_summary, [[
                "Layer 3.2 inclusion", "Disabled", "not assessed",
            ]])
            self.layer32_provenance.setPlainText(
                "Layer 3.2 is disabled in the current case configuration. Stored historical evidence, if any, is not presented or exported as current."
            )
            self.layer32_viewer_status.setText("Layer 3.2 is disabled; the biological field viewer is unavailable.")
            return
        dependencies_current = (
            case.layer1.calculation_status in {"completed", "completed_with_warnings"}
            and case.layer2_2.calculation_status in {"completed", "completed_with_warnings"}
            and case.layer3_1.calculation_status in {"completed", "completed_with_warnings"}
        )
        current = record.calculation_status in {"completed", "completed_with_warnings"} and dependencies_current
        result = (record.result or {}) if current else {}
        self.layer32_status_pill.set_status(record.calculation_status)
        self.layer32_interpretation_pill.set_status(record.interpretation_status)
        self.layer32_status_text.setText(
            f"Run {record.run_id or '—'}  ·  {len(result.get('edge_metrics', []))} edge(s)  ·  "
            f"{len(result.get('oar_biological_spill', []))} OAR result(s)"
        )
        warnings = list(result.get("warnings", record.warnings))
        if record.calculation_status == "stale":
            warnings.insert(0, record.stale_reason or "Layer 3.2 result is stale.")
        if not dependencies_current:
            warnings.insert(0, "Current Layer 1, Layer 2.2, and Layer 3.1 results are required; stored Layer 3.2 values are hidden.")
        if record.error:
            warnings.insert(0, record.error)
        self.layer32_warnings.set_messages(
            warnings, blocked=canonical_state(record.calculation_status) in {"BLOCKED", "INVALID"},
        )
        model = result.get("model", {})
        parameters = model.get("parameters") or resolved_parameters(case.configuration.layer32_parameters)
        rows = model.get("parameter_rows") or [
            {"parameter": key, "value": value, "units": "configured", "source": "current case configuration"}
            for key, value in parameters.items()
        ]
        parameter_display_names = {
            "hazard_weight_ros": "ROS-like mediator weight",
            "hazard_weight_cytokine": "Cytokine-like mediator weight",
            "nonlocal_scaling": "Non-local exposure scaling s",
        }
        _set_table(self.layer32_parameter_table, [[
            parameter_display_names.get(str(item.get("parameter")), item.get("parameter")),
            item.get("value"), item.get("units"), item.get("source"),
        ] for item in rows])
        _set_table(self.layer32_configuration_summary, [
            ["Non-local scaling s", parameters.get("nonlocal_scaling"), "dimensionless"],
            ["ROS-like weight", parameters.get("hazard_weight_ros"), "dimensionless"],
            ["Cytokine-like weight", parameters.get("hazard_weight_cytokine"), "dimensionless"],
            ["ROS diffusion", parameters.get("diffusion_ros_mm2_per_time"), "mm²/model-time"],
            ["Cytokine diffusion", parameters.get("diffusion_cytokine_mm2_per_time"), "mm²/model-time"],
            ["Vascular sink", "Disabled", "no vessel geometry or uptake model"],
            ["Parameter-set version", model.get("parameter_set_version", "not recorded"), "versioned"],
            ["Calculation status", record.calculation_status, record.interpretation_status],
        ])
        _set_table(self.layer32_scenario_table, [[
            item.get("label"), item.get("status"), item.get("definition") or item.get("reason"),
        ] for item in result.get("comparison_scenarios", [])], "No stored comparison-scenario record is available.")
        summary = result.get("graph_summary", {})
        _set_table(self.layer32_graph_summary, [[
            "Physical plan iPVDR median", summary.get("physical_plan_ipvdr_median"), "Stored Layer 2.2 absorbed-dose graph endpoint",
        ], [
            "Baseline LQ effect-equivalent iPVDR median", summary.get("baseline_lq_effect_equivalent_ipvdr_median"), "Fraction-history-aware alpha P + beta Q baseline",
        ], [
            "Biological effect-equivalent iPVDR median", summary.get("biological_effect_equivalent_ipvdr_median"), "Same graph and 3 mm valley spheres on final effect-equivalent field",
        ], [
            "Biological iPVDR shift", summary.get("biological_ipvdr_shift"), "Signed biological minus physical value",
        ], [
            "Non-local-only iPVDR shift", summary.get("nonlocal_only_ipvdr_shift"), "Final biological minus baseline LQ effect-equivalent value",
        ]] if summary else [], "Run Layer 3.2 to calculate the biological graph reinterpretation.")
        _set_table(self.layer32_edge_table, [[
            item.get("edge_id"), " — ".join(item.get("nodes", [])), item.get("physical_ipvdr"),
            item.get("baseline_lq_effect_equivalent_ipvdr"), item.get("biological_effect_equivalent_ipvdr"),
            item.get("biological_ipvdr_shift"), item.get("nonlocal_only_ipvdr_shift"),
            item.get("valley_effect_shift_gy_equivalent"),
        ] for item in result.get("edge_metrics", [])], "No Layer 3.2 edge metrics are available.")
        gtv = result.get("gtv_biological_context", {})
        _set_table(self.layer32_gtv_table, [[
            key.replace("_", " "), endpoint.get("mean"), endpoint.get("d95"), endpoint.get("d50"),
            endpoint.get("d2"), endpoint.get("units"),
        ] for key, endpoint in gtv.items() if key != "cumulative_nonlocal_hazard"],
            "No whole-GTV Layer 3.2 context is available.")
        _set_table(self.layer32_shell_table, [[
            f"{item.get('shell_mm', [None, None])[0]:g}–{item.get('shell_mm', [None, None])[1]:g}",
            item.get("voxel_count"), item.get("physical_absorbed_dose", {}).get("mean"),
            item.get("biological_effect_equivalent_dose", {}).get("mean"),
            item.get("additional_model_derived_effect_equivalent_dose", {}).get("mean"),
            item.get("final_survival_fraction", {}).get("mean"),
        ] for item in result.get("peri_gtv_spill_shells", [])], "No peri-GTV spill-shell results are available.")
        _set_table(self.layer32_oar_table, [[
            item.get("oar_name"), item.get("classification"), item.get("nearest_vertex_id"),
            item.get("nearest_vertex_distance_mm"), item.get("physical_absorbed_dose", {}).get("mean"),
            item.get("biological_effect_equivalent_dose", {}).get("mean"),
            item.get("biological_effect_equivalent_dose", {}).get("d2"),
            item.get("additional_model_derived_effect_equivalent_dose", {}).get("mean"),
            item.get("compliance_assessment"),
        ] for item in result.get("oar_biological_spill", [])], "No configured rasterised OAR spill results are available.")
        assay = result.get("assay_observables", {})
        _set_table(self.layer32_assay_table, [[
            key.replace("_", " "), item.get("mean"), item.get("maximum"), item.get("units"), assay.get("scope"),
        ] for key, item in assay.items() if isinstance(item, dict)], "No stored model observables are available.")
        _set_table(self.layer32_regional_table, [[
            item.get("display_name"),
            item.get("mean_cumulative_mediator_exposure_h"),
            item.get("p95_cumulative_mediator_exposure_h"),
            item.get("mean_additional_modelled_survival_reduction_percent"),
            item.get("maximum_additional_modelled_survival_reduction_percent"),
            item.get("volume_at_least_5pct_reduction_cc"),
            item.get("mean_final_survival_change_absolute"),
        ] for item in result.get("modelled_regional_exposure_and_consequence", [])],
            "No regional exposure and consequence records are available.")
        self.layer32_provenance.setPlainText(self._json_or_state({
            "scientific_position": result.get("scientific_position"),
            "physical_dose_mutated": result.get("physical_dose_mutated"),
            "model": model, "geometry": result.get("geometry"), "artifacts": result.get("artifacts"),
            "provenance": result.get("provenance"),
        } if result else None, "No Layer 3.2 provenance is available."))
        self._update_layer32_enabled_controls(enabled)
        if self.layer32_viewer_run_id and self.layer32_viewer_run_id != record.run_id:
            self.layer32_viewer_status.setText("STALE — Layer 3.2 changed. Rebuild the stored-field viewer.")
            if self.layer32_viewer is not None:
                self.layer32_viewer.setEnabled(False)

    @staticmethod
    def _metric_endpoint_display(endpoint: dict[str, Any] | None) -> str:
        if not endpoint or endpoint.get("value") is None:
            return "—"
        value = endpoint.get("value")
        text = f"{float(value):.6f}".rstrip("0").rstrip(".") if isinstance(value, (int, float)) else str(value)
        return f"{text} {endpoint.get('units', '')}".strip()


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
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    launch()
