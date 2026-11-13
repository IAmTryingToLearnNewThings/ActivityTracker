"""Main window and navigation for ActivityTracker."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.autostart import WindowsAutostart
from core.database import Database
from core.monitor import ActivityMonitor
from gui.dashboard import Dashboard
from gui.statistics import Statistics
from gui.tray import TrayController


class MainWindow(QMainWindow):
    def __init__(
        self,
        database: Database,
        monitor: ActivityMonitor,
        autostart: WindowsAutostart,
        on_exit: Callable[[], None],
        settings_panel: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("ActivityTracker")
        self.setMinimumSize(900, 620)
        self.resize(1180, 780)
        self._allow_close = False

        central = QWidget()
        central.setStyleSheet("background: #f3f6fa; color: #172338;")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setFixedWidth(205)
        sidebar.setStyleSheet(
            "QFrame { background: #172338; } "
            "QLabel { color: white; } "
            "QPushButton { color: #d7dfeb; text-align: left; border: 0; "
            "padding: 11px 14px; border-radius: 6px; } "
            "QPushButton:hover, QPushButton:checked { background: #2a3a55; color: white; }"
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 20, 14, 14)
        sidebar_layout.setSpacing(8)
        brand = QLabel("ActivityTracker")
        brand.setStyleSheet("font-size: 18px; font-weight: 700; padding: 8px;")
        sidebar_layout.addWidget(brand)

        self.pages = QStackedWidget()
        self.dashboard = Dashboard(database)
        self.statistics = Statistics(database)
        self.pages.addWidget(self.dashboard)
        self.pages.addWidget(self.statistics)
        page_definitions = [("Pulpit", self.dashboard), ("Statystyki", self.statistics)]
        if settings_panel is not None:
            page_definitions.append(("Ustawienia", settings_panel))
        for _, page in page_definitions[2:]:
            self.pages.addWidget(page)

        self.navigation_buttons: list[QPushButton] = []
        for index, (label, _) in enumerate(page_definitions):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, page=index: self._select_page(page)
            )
            sidebar_layout.addWidget(button)
            self.navigation_buttons.append(button)
        sidebar_layout.addStretch(1)
        sidebar_layout.addWidget(
            QLabel("Dane są przechowywane lokalnie.")
        )

        layout.addWidget(sidebar)
        layout.addWidget(self.pages, stretch=1)
        self.setCentralWidget(central)
        self._select_page(0)
        self.tray = TrayController(self, monitor, autostart, on_exit)

    def _select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.navigation_buttons):
            button.setChecked(button_index == index)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if self.tray.available:
            self.hide()
        else:
            self.showMinimized()
        event.ignore()

    def close_for_exit(self) -> None:
        self._allow_close = True
        self.tray.hide()
        self.close()
