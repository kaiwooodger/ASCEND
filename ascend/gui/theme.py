"""Shared Qt visual system and normalized workstation status presentation."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


METRIC_LABELS = {
    "peripheral_coverage_v95_rxl": "Peripheral coverage",
    "high_dose_coverage_v95_rxh": "High-dose coverage",
    "high_dose_volume_fraction": "High-dose volume fraction",
    "mean_peak_dose": "Mean peak dose",
    "mean_valley_dose": "Mean valley dose",
    "structure_based_dose_ratio": "Peak-to-valley ratio",
}


def canonical_state(value: Any, *, warnings: bool = False) -> str:
    """Handle canonical state for the enclosing ASCEND workflow."""
    text = str(value or "").strip().lower().replace(" ", "_")
    if text in {"pass", "passed", "completed", "valid", "available", "protocol_interpretable"}:
        return "WARN" if warnings else "PASS"
    if text in {"warn", "warning", "completed_with_warnings", "available_with_warnings", "provisional"}:
        return "PROVISIONAL" if text == "provisional" else "WARN"
    if text in {"block", "blocked", "failed", "invalid", "not_interpretable"}:
        return "BLOCKED" if text != "invalid" else "INVALID"
    if text in {"outside_validated_scope"}:
        return "OUTSIDE SCOPE"
    if text in {"not_applicable", "not-applicable"}:
        return "NOT APPLICABLE"
    if text in {"not_implemented"}:
        return "NOT IMPLEMENTED"
    if text in {"stale"}:
        return "STALE"
    if text in {"not_run", "not_calculated", "", "none"}:
        return "NOT RUN"
    return text.replace("_", " ").upper()


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class StatusPill(QLabel):
    """Represent status pill state and behavior."""
    def __init__(self, text: str = "NOT RUN") -> None:
        super().__init__()
        self.setObjectName("statusPill")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(72)
        self.set_status(text)

    def set_status(self, value: Any, *, warnings: bool = False) -> None:
        """Update status presentation state."""
        state = canonical_state(value, warnings=warnings)
        self.setText(state)
        self.setProperty("state", state.replace(" ", "_"))
        _refresh_style(self)


class WarningBanner(QFrame):
    """Represent warning banner state and behavior."""
    def __init__(self, empty_text: str = "No warnings recorded.") -> None:
        super().__init__()
        self.setObjectName("warningBanner")
        self.empty_text = empty_text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.badge = StatusPill("PASS")
        self.detail = QLabel(empty_text)
        self.detail.setObjectName("warningDetail")
        self.detail.setWordWrap(True)
        layout.addWidget(self.badge, 0, Qt.AlignTop)
        layout.addWidget(self.detail, 1)
        self.set_messages([])

    def set_messages(self, messages: list[Any], *, blocked: bool = False) -> None:
        """Update messages presentation state."""
        cleaned = [str(item) for item in messages if str(item).strip()]
        state = "BLOCKED" if blocked else "WARN" if cleaned else "PASS"
        self.badge.set_status(state)
        self.detail.setText("\n".join(cleaned) if cleaned else self.empty_text)
        self.setProperty("state", state)
        _refresh_style(self)


class MetricCard(QFrame):
    """Represent metric card state and behavior."""
    def __init__(self, metric_id: str) -> None:
        super().__init__()
        self.metric_id = metric_id
        self.setObjectName("metricCard")
        self.setMinimumHeight(110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)
        top = QHBoxLayout()
        self.title = QLabel(METRIC_LABELS.get(metric_id, metric_id.replace("_", " ").title()))
        self.title.setObjectName("metricTitle")
        self.state = StatusPill("NOT RUN")
        top.addWidget(self.title, 1)
        top.addWidget(self.state)
        layout.addLayout(top)
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        layout.addWidget(self.value)
        self.detail = QLabel("No result")
        self.detail.setObjectName("metricDetail")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    @staticmethod
    def _display_value(value: Any, units: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            number = f"{float(value):.3f}".rstrip("0").rstrip(".")
        else:
            number = str(value)
        return f"{number} {units}".strip()

    def set_metric(self, metric: dict[str, Any] | None) -> None:
        """Update metric presentation state."""
        if not metric:
            self.value.setText("—")
            self.detail.setText("No result")
            self.state.set_status("not_run")
            self.setProperty("state", "NOT_RUN")
            _refresh_style(self)
            return
        applicability = metric.get("applicability")
        warnings = [str(item) for item in metric.get("warnings", [])]
        self.value.setText(self._display_value(metric.get("value"), metric.get("units")))
        self.detail.setText("; ".join(warnings) if warnings else "Stored locked metric")
        state = canonical_state(applicability, warnings=bool(warnings))
        self.state.set_status(state)
        self.setProperty("state", state.replace(" ", "_"))
        _refresh_style(self)


class StatePanel(QFrame):
    """Represent state panel state and behavior."""
    def __init__(self, state: str, title: str, detail: str) -> None:
        super().__init__()
        self.setObjectName("statePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        badge = StatusPill(state)
        heading = QLabel(title)
        heading.setObjectName("stateTitle")
        body = QLabel(detail)
        body.setObjectName("stateDetail")
        body.setWordWrap(True)
        layout.addWidget(badge, 0, Qt.AlignLeft)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch()


def workstation_stylesheet(dark: bool = False) -> str:
    """Handle workstation stylesheet for the enclosing ASCEND workflow."""
    if dark:
        c = {
            "window": "#10151c", "surface": "#18202a", "surface2": "#202b37", "sidebar": "#121923",
            "text": "#e8eef5", "muted": "#9eacba", "border": "#344252", "primary": "#4f9ddb",
            "primary_hover": "#66afe6", "header": "#0b1118", "selection": "#245f91",
            "pass_bg": "#15392d", "pass_fg": "#7ee2b8", "warn_bg": "#493618", "warn_fg": "#ffd27a",
            "block_bg": "#4a2025", "block_fg": "#ff9b9b", "neutral_bg": "#293544", "neutral_fg": "#c7d2de",
            "provisional_bg": "#2f2852", "provisional_fg": "#c7b8ff",
        }
    else:
        c = {
            "window": "#f3f6f9", "surface": "#ffffff", "surface2": "#f7f9fb", "sidebar": "#e9eef3",
            "text": "#152231", "muted": "#5c6b7a", "border": "#cbd5df", "primary": "#1769a6",
            "primary_hover": "#125687", "header": "#132231", "selection": "#1f67a1",
            "pass_bg": "#d9f2e7", "pass_fg": "#166743", "warn_bg": "#fff0cc", "warn_fg": "#825300",
            "block_bg": "#fde1e1", "block_fg": "#9d2424", "neutral_bg": "#e5ebf1", "neutral_fg": "#526273",
            "provisional_bg": "#e8e2fb", "provisional_fg": "#5a3d9a",
        }
    return f"""
QMainWindow, QWidget {{ background: {c['window']}; color: {c['text']}; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 13px; }}
QLabel {{ background: transparent; }}
QFrame#topBar {{ background: {c['header']}; border: 0; }}
QFrame#topBar QLabel {{ background: transparent; color: #f5f8fb; }}
QLabel#brand {{ font-size: 19px; font-weight: 750; letter-spacing: 0.5px; }}
QLabel#caseName {{ font-size: 14px; font-weight: 650; }}
QLabel#caseContext {{ color: #b7c3ce; font-size: 11px; }}
QFrame#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QLabel#navigationTitle {{ color: {c['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; padding: 2px 8px 8px 8px; }}
QListWidget#navigation {{ background: transparent; border: 0; outline: 0; }}
QListWidget#navigation::item {{ padding: 9px 12px; margin: 1px 4px; border-radius: 5px; }}
QListWidget#navigation::item:selected {{ background: {c['selection']}; color: white; font-weight: 650; }}
QListWidget#navigation::item:disabled {{ color: {c['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; padding-top: 17px; }}
QLabel#sidebarCase {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 10px; color: {c['muted']}; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLabel#title {{ font-size: 25px; font-weight: 750; }}
QLabel#subtitle {{ color: {c['muted']}; font-size: 13px; padding-bottom: 5px; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; }}
QLabel#sectionDescription {{ color: {c['muted']}; font-size: 12px; }}
QFrame#card, QFrame#metricCard, QFrame#statePanel {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 7px; }}
QFrame#metricCard[state="WARN"], QFrame#metricCard[state="INVALID"], QFrame#metricCard[state="NOT_APPLICABLE"] {{ border-left: 4px solid {c['warn_fg']}; }}
QFrame#metricCard[state="BLOCKED"] {{ border-left: 4px solid {c['block_fg']}; }}
QLabel#metricTitle {{ font-size: 12px; font-weight: 650; color: {c['muted']}; }}
QLabel#metricValue {{ font-size: 24px; font-weight: 750; }}
QLabel#metricDetail {{ color: {c['muted']}; font-size: 11px; }}
QLabel#stateTitle {{ font-size: 22px; font-weight: 750; }}
QLabel#stateDetail {{ color: {c['muted']}; font-size: 13px; }}
QLabel#statusPill {{ border-radius: 9px; padding: 3px 8px; font-size: 10px; font-weight: 750; }}
QLabel#statusPill[state="PASS"] {{ background: {c['pass_bg']}; color: {c['pass_fg']}; }}
QLabel#statusPill[state="WARN"] {{ background: {c['warn_bg']}; color: {c['warn_fg']}; }}
QLabel#statusPill[state="BLOCKED"], QLabel#statusPill[state="INVALID"] {{ background: {c['block_bg']}; color: {c['block_fg']}; }}
QLabel#statusPill[state="PROVISIONAL"] {{ background: {c['provisional_bg']}; color: {c['provisional_fg']}; }}
QLabel#statusPill[state="NOT_RUN"], QLabel#statusPill[state="NOT_APPLICABLE"], QLabel#statusPill[state="NOT_IMPLEMENTED"], QLabel#statusPill[state="STALE"], QLabel#statusPill[state="OUTSIDE_SCOPE"] {{ background: {c['neutral_bg']}; color: {c['neutral_fg']}; }}
QFrame#warningBanner {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 6px; }}
QFrame#warningBanner[state="WARN"] {{ background: {c['warn_bg']}; border-color: {c['warn_fg']}; }}
QFrame#warningBanner[state="BLOCKED"] {{ background: {c['block_bg']}; border-color: {c['block_fg']}; }}
QLabel#warningDetail {{ background: transparent; }}
QPushButton {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 7px 12px; font-weight: 600; }}
QPushButton:hover {{ border-color: {c['primary']}; color: {c['primary']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['neutral_bg']}; }}
QPushButton#primary {{ background: {c['primary']}; color: white; border-color: {c['primary']}; }}
QPushButton#primary:hover {{ background: {c['primary_hover']}; color: white; }}
QLineEdit, QComboBox, QTextEdit, QTableWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 5px; selection-background-color: {c['selection']}; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QTableWidget:focus {{ border: 1px solid {c['primary']}; }}
QCheckBox {{ spacing: 7px; }}
QTableWidget {{ gridline-color: {c['border']}; alternate-background-color: {c['surface2']}; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['text']}; padding: 7px; border: 0; border-bottom: 1px solid {c['border']}; font-weight: 650; }}
QTabWidget::pane, QToolBox::tab {{ border: 1px solid {c['border']}; background: {c['surface']}; }}
QTabBar::tab {{ background: {c['surface2']}; padding: 8px 14px; border: 1px solid {c['border']}; border-bottom: 0; }}
QTabBar::tab:selected {{ background: {c['surface']}; color: {c['primary']}; font-weight: 650; }}
QWidget#unifiedViewerWorkspace, QWidget#fourPaneViewport, QWidget#cadMetricStrip {{ background: #071a38; }}
QFrame#viewerPane {{ background: #071a38; border: 1px solid #2a4a6d; border-radius: 0; }}
QLabel#viewerPaneTitle {{ background: #0d274a; color: #edf5fc; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; }}
QTabBar#viewerOverlayTabs {{ background: #071a38; }}
QTabBar#viewerOverlayTabs::tab {{ background: #102b4d; color: #b8cadc; border-color: #31567d; padding: 5px 12px; }}
QTabBar#viewerOverlayTabs::tab:selected {{ background: #1769a6; color: white; }}
QTabBar#viewerOverlayTabs::tab:disabled {{ color: #5f7892; background: #0b203a; }}
QFrame#linkedNavigation {{ background: #0b203a; border: 1px solid #2a4a6d; }}
QFrame#linkedNavigation QLabel {{ color: #c8d8e8; }}
QWidget#cadMetricStrip QLabel#metricCard {{ background: #102b4d; color: #edf5fc; border: 0; border-radius: 0; font-size: 10px; }}
QSlider#viewerSliceSlider {{ background: #0b203a; min-height: 18px; }}
QToolBox::tab {{ min-height: 26px; padding: 6px 12px; border-radius: 4px; font-weight: 650; }}
QToolBox::tab:selected {{ color: {c['primary']}; }}
QStatusBar {{ background: {c['header']}; color: #dbe5ee; border: 0; }}
QStatusBar QLabel {{ background: transparent; color: #dbe5ee; padding: 0 8px; }}
"""
