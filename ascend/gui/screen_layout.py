"""Monitor-aware window presentation for ASCEND graphical workspaces."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget


def show_maximised_on_current_screen(window: QWidget) -> None:
    """Use the complete available area of the window's current monitor.

    Setting the available geometry before requesting the native maximised state
    gives consistent first-frame sizing on Windows, Linux, and macOS while
    leaving taskbars, docks, and desktop panels unobscured.
    """

    screen = window.screen() or QApplication.primaryScreen()
    if screen is not None:
        window.setGeometry(screen.availableGeometry())
    window.showMaximized()
