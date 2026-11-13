import os
import smtplib
import threading
import time as time_module
from datetime import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication

from config.settings import AppSettings
from core.database import Database
from gui.settings import SettingsPanel


@pytest.fixture(scope="session")
def qt_application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def database():
    instance = Database(":memory:")
    try:
        yield instance
    finally:
        instance.close()


class MemoryCredentials:
    def __init__(self):
        self.passwords = {}

    def set_password(self, username, password):
        self.passwords[username] = password


def test_settings_are_saved_and_password_stays_out_of_sqlite(
    database, qt_application
):
    credentials = MemoryCredentials()
    saved_settings = []
    panel = SettingsPanel(
        database, credentials, AppSettings(), saved_settings.append
    )
    try:
        panel.idle_timeout_minutes.setValue(8)
        panel.ignored_processes.setText("game.exe, editor.exe")
        panel.auto_reports_enabled.setChecked(True)
        panel.smtp_host.setText("smtp.example.com")
        panel.smtp_sender.setText("tracker@example.com")
        panel.smtp_recipient.setText("user@example.com")
        panel.smtp_username.setText("account@example.com")
        panel.smtp_password.setText("private-password")
        panel.daily_report_time.setTime(QTime(19, 30))
        panel.weekly_report_day.setCurrentIndex(4)
        panel.weekly_report_time.setTime(QTime(10, 15))

        panel.save()

        loaded = AppSettings.load(database)
        assert loaded.idle_timeout_seconds == 480
        assert loaded.ignored_processes == ("game.exe", "editor.exe")
        assert loaded.auto_reports_enabled
        assert loaded.daily_report_time == time(19, 30)
        assert loaded.weekly_report_day == 4
        assert loaded.weekly_report_time == time(10, 15)
        assert credentials.passwords == {"account@example.com": "private-password"}
        assert "private-password" not in repr(database.get_report_history())
        assert database.get_setting("smtp_password") is None
        assert saved_settings == [loaded]
        assert panel.smtp_password.text() == ""
    finally:
        panel.close()


def test_invalid_auto_report_settings_are_not_saved(database, qt_application):
    panel = SettingsPanel(
        database, MemoryCredentials(), AppSettings(), lambda settings: None
    )
    try:
        panel.auto_reports_enabled.setChecked(True)
        panel.save()

        assert "Nie udało się" in panel.status_label.text()
        assert database.get_setting("auto_reports_enabled") is None
    finally:
        panel.close()


def test_test_email_uses_current_form_values_without_saving_settings(
    database, qt_application
):
    class FakeEmailSender:
        def __init__(self):
            self.called = threading.Event()
            self.config = None

        def send_test(self, config):
            self.config = config
            self.called.set()

    credentials = MemoryCredentials()
    sender = FakeEmailSender()
    panel = SettingsPanel(
        database,
        credentials,
        AppSettings(),
        lambda settings: None,
        email_sender=sender,
    )
    try:
        panel.smtp_host.setText("smtp.example.com")
        panel.smtp_sender.setText("tracker@example.com")
        panel.smtp_recipient.setText("user@example.com")
        panel.smtp_username.setText("account@example.com")
        panel.smtp_password.setText("private-password")

        panel.test_email_button.click()

        assert sender.called.wait(timeout=2)
        deadline = time_module.monotonic() + 2
        while not panel.test_email_button.isEnabled() and time_module.monotonic() < deadline:
            qt_application.processEvents()
            time_module.sleep(0.01)

        assert panel.test_email_button.isEnabled()
        assert panel.status_label.text() == "Wiadomość testowa została wysłana."
        assert sender.config.host == "smtp.example.com"
        assert sender.config.recipient == "user@example.com"
        assert credentials.passwords == {"account@example.com": "private-password"}
        assert database.get_setting("smtp_host") is None
        assert database.get_setting("smtp_password") is None
        assert panel.smtp_password.text() == ""
    finally:
        panel.close()


def test_test_email_reports_invalid_smtp_configuration(database, qt_application):
    panel = SettingsPanel(
        database, MemoryCredentials(), AppSettings(), lambda settings: None
    )
    try:
        panel.test_email_button.click()

        assert "Nie udało się wysłać" in panel.status_label.text()
        assert panel.test_email_button.isEnabled()
    finally:
        panel.close()


def test_test_email_reports_smtp_failure(database, qt_application):
    class FailedEmailSender:
        def send_test(self, config):
            raise smtplib.SMTPException("test failure")

    panel = SettingsPanel(
        database,
        MemoryCredentials(),
        AppSettings(),
        lambda settings: None,
        email_sender=FailedEmailSender(),
    )
    try:
        panel.smtp_host.setText("smtp.example.com")
        panel.smtp_sender.setText("tracker@example.com")
        panel.smtp_recipient.setText("user@example.com")
        panel.test_email_button.click()

        deadline = time_module.monotonic() + 2
        while not panel.test_email_button.isEnabled() and time_module.monotonic() < deadline:
            qt_application.processEvents()
            time_module.sleep(0.01)

        assert panel.test_email_button.isEnabled()
        assert panel.status_label.text() == "Nie udało się wysłać wiadomości testowej."
    finally:
        panel.close()


def test_app_settings_round_trip_and_validation(database):
    settings = AppSettings(
        ignored_processes=("browser.exe", "game.exe"),
        weekly_report_day=2,
    )
    settings.save(database)
    assert AppSettings.load(database) == settings

    with pytest.raises(ValueError, match="Weekly report day"):
        AppSettings(weekly_report_day=7)


def test_legacy_comma_separated_ignored_process_setting_is_read(database):
    database.set_setting("ignored_processes", "browser.exe, editor.exe")

    assert AppSettings.load(database).ignored_processes == (
        "browser.exe",
        "editor.exe",
    )
