"""System tray controls for ActivityTracker."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from core.autostart import WindowsAutostart
from core.monitor import ActivityMonitor

logger = logging.getLogger(__name__)


class TrayController:
    def __init__(
        self,
        window: QWidget,
        monitor: ActivityMonitor,
        autostart: WindowsAutostart,
        on_exit: Callable[[], None],
    ) -> None:
        self._window = window
        self._monitor = monitor
        self._autostart = autostart
        self._on_exit = on_exit
        self.icon = QSystemTrayIcon(self._create_icon(), window)
        self.icon.setToolTip("ActivityTracker — monitorowanie aktywności")

        menu = QMenu(window)
        open_action = menu.addAction("Otwórz ActivityTracker")
        open_action.triggered.connect(self.open_window)
        menu.addSeparator()
        self.pause_action = menu.addAction("Wstrzymaj monitorowanie")
        self.pause_action.triggered.connect(self._pause)
        self.resume_action = menu.addAction("Wznów monitorowanie")
        self.resume_action.triggered.connect(self._resume)
        menu.addSeparator()
        self.autostart_action = menu.addAction("Uruchamiaj po zalogowaniu")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self._autostart.enabled)
        self.autostart_action.toggled.connect(self._set_autostart)
        menu.addSeparator()
        exit_action = menu.addAction("Zakończ program")
        exit_action.triggered.connect(self._on_exit)

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        if self.available:
            self.icon.show()
        else:
            logger.warning("Windows system tray is unavailable.")
        self._sync_monitor_actions()

    @staticmethod
    def _create_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#3867d6"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(3, 3, 58, 58)
        painter.setPen(QPen(QColor("#ffffff"), 4, Qt.PenStyle.SolidLine))
        painter.drawEllipse(17, 15, 30, 30)
        painter.drawLine(32, 20, 32, 30)
        painter.drawLine(32, 30, 40, 35)
        painter.end()
        return QIcon(pixmap)

    def open_window(self) -> None:
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.open_window()

    def _pause(self) -> None:
        try:
            self._monitor.pause()
        except (OSError, sqlite3.Error):
            logger.exception("Could not pause activity monitoring.")
            self._show_error("Monitorowanie nie zostało wstrzymane.")
        self._sync_monitor_actions()

    def _resume(self) -> None:
        self._monitor.resume()
        self._sync_monitor_actions()

    def _sync_monitor_actions(self) -> None:
        paused = self._monitor.is_paused
        self.pause_action.setEnabled(not paused)
        self.resume_action.setEnabled(paused)

    def _set_autostart(self, enabled: bool) -> None:
        try:
            if enabled:
                self._autostart.enable()
            else:
                self._autostart.disable()
        except OSError:
            logger.exception("Could not update ActivityTracker autostart.")
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not enabled)
            self.autostart_action.blockSignals(False)
            self._show_error("Nie udało się zmienić ustawienia autostartu.")

    def _show_error(self, message: str) -> None:
        if self.available:
            self.icon.showMessage(
                "ActivityTracker", message, QSystemTrayIcon.MessageIcon.Critical
            )

    def hide(self) -> None:
        self.icon.hide()
