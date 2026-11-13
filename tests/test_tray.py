import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from core.autostart import WindowsAutostart
from gui.tray import TrayController


@pytest.fixture(scope="session")
def qt_application():
    return QApplication.instance() or QApplication([])


class FakeMonitor:
    def __init__(self):
        self.is_paused = False

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False


def test_tray_actions_pause_resume_and_exit(tmp_path, qt_application):
    window = QWidget()
    monitor = FakeMonitor()
    exited = []
    autostart = WindowsAutostart(
        startup_dir=tmp_path, launch_command=("ActivityTracker.exe",)
    )
    tray = TrayController(window, monitor, autostart, lambda: exited.append(True))
    try:
        assert tray.pause_action.isEnabled()
        assert not tray.resume_action.isEnabled()

        tray.pause_action.trigger()
        assert monitor.is_paused
        assert not tray.pause_action.isEnabled()
        assert tray.resume_action.isEnabled()

        tray.resume_action.trigger()
        assert not monitor.is_paused

        tray.autostart_action.trigger()
        assert autostart.enabled
        tray.autostart_action.trigger()
        assert not autostart.enabled

        tray.icon.contextMenu().actions()[-1].trigger()
        assert exited == [True]
    finally:
        tray.hide()
        window.close()
