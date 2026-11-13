"""ActivityTracker application entry point."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from core.autostart import WindowsAutostart
from core.database import Database
from core.idle_detector import IdleDetector
from core.monitor import ActivityMonitor
from core.process_manager import ProcessManager
from core.scheduler import ReportScheduler
from config.settings import AppSettings
from gui.main_window import MainWindow
from gui.settings import SettingsPanel
from reports.daily_report import DailyReport
from reports.email_sender import CredentialStore, EmailSender
from reports.weekly_report import WeeklyReport

logger = logging.getLogger("activity_tracker")


def _data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("The current user's LOCALAPPDATA directory is unavailable.")
    directory = Path(local_app_data) / "ActivityTracker"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _configure_logging(directory: Path) -> None:
    handler = RotatingFileHandler(
        directory / "activity-tracker.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
    )


def main() -> int:
    if os.name != "nt":
        raise OSError("ActivityTracker is a Windows-only desktop application.")

    app = QApplication(sys.argv)
    app.setApplicationName("ActivityTracker")
    app.setQuitOnLastWindowClosed(False)

    database: Database | None = None
    monitor: ActivityMonitor | None = None
    scheduler: ReportScheduler | None = None
    window: MainWindow | None = None
    try:
        data_directory = _data_directory()
        _configure_logging(data_directory)
        database = Database(data_directory / "activity-tracker.sqlite3")
        database.recover_open_sessions()

        settings_ref = {"settings": AppSettings.load(database)}
        process_manager = ProcessManager(
            ignored_processes=settings_ref["settings"].ignored_processes
        )
        idle_detector = IdleDetector()
        credential_store = CredentialStore()
        email_sender = EmailSender(credential_store)

        def update_settings(settings: AppSettings) -> None:
            settings_ref["settings"] = settings
            process_manager.set_ignored_processes(settings.ignored_processes)

        scheduler = ReportScheduler(
            database,
            DailyReport(database),
            WeeklyReport(database),
            email_sender,
            settings_provider=lambda: settings_ref["settings"],
        )
        settings_panel = SettingsPanel(
            database,
            credential_store,
            settings_ref["settings"],
            on_saved=update_settings,
            email_sender=email_sender,
        )
        monitor = ActivityMonitor(
            database,
            process_manager,
            is_tracking_allowed=lambda: idle_detector.is_tracking_allowed(
                settings_ref["settings"].idle_timeout_seconds
            ),
        )
        autostart = WindowsAutostart()
        window = MainWindow(
            database,
            monitor,
            autostart,
            on_exit=app.quit,
            settings_panel=settings_panel,
        )
        monitor.start()
        scheduler.start()
        if "--background" in sys.argv[1:] and window.tray.available:
            window.hide()
        else:
            window.show()

    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        logger.exception("ActivityTracker could not start.")
        QMessageBox.critical(None, "ActivityTracker", str(exc))
        if database is not None:
            database.close()
        return 1

    def shutdown() -> None:
        if window is not None:
            window.tray.hide()
        can_close_database = True
        if scheduler is not None:
            try:
                scheduler.stop()
            except TimeoutError:
                logger.exception("Report scheduler did not shut down cleanly.")
                can_close_database = not scheduler.is_running
        if monitor is not None:
            try:
                monitor.stop()
            except (OSError, sqlite3.Error, TimeoutError):
                logger.exception("Activity monitor did not shut down cleanly.")
                can_close_database = can_close_database and not monitor.is_running
        if database is not None and can_close_database:
            database.close()

    app.aboutToQuit.connect(shutdown)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
