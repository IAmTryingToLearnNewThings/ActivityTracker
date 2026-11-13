import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from core.autostart import WindowsAutostart
from core.database import Database
from core.monitor import ActivityMonitor
from gui.main_window import MainWindow


@pytest.fixture(scope="session")
def qt_application():
    return QApplication.instance() or QApplication([])


class EmptyProcessManager:
    def current_process(self):
        return None


def test_main_window_navigates_and_preserves_close_to_tray_behavior(
    tmp_path, qt_application
):
    database = Database(":memory:")
    monitor = ActivityMonitor(database, EmptyProcessManager())
    autostart = WindowsAutostart(
        startup_dir=tmp_path, launch_command=("ActivityTracker.exe",)
    )
    settings_panel = QWidget()
    window = MainWindow(
        database,
        monitor,
        autostart,
        lambda: None,
        settings_panel=settings_panel,
    )
    try:
        assert window.pages.count() == 3
        window._select_page(1)
        assert window.pages.currentWidget() is window.statistics
        window._select_page(2)
        assert window.pages.currentWidget() is settings_panel
        window.show()
        window.close()
        if window.tray.available:
            assert not window.isVisible()
        else:
            assert window.isMinimized()
    finally:
        window.close_for_exit()
        database.close()
