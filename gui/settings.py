"""User settings form for monitoring and scheduled email reports."""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from collections.abc import Callable

import keyring
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from config.settings import AppSettings
from core.database import Database
from reports.email_sender import (
    CredentialStore,
    EmailSender,
    MissingCredentialError,
    SMTPConfig,
)

logger = logging.getLogger(__name__)

_WEEKDAYS = (
    "Poniedziałek",
    "Wtorek",
    "Środa",
    "Czwartek",
    "Piątek",
    "Sobota",
    "Niedziela",
)

_EMAIL_ERRORS = (
    OSError,
    ValueError,
    smtplib.SMTPException,
    MissingCredentialError,
    keyring.errors.KeyringError,
)


class _TestEmailSignals(QObject):
    finished = Signal(bool)


class _TestEmailWorker(QRunnable):
    def __init__(self, email_sender: EmailSender, config: SMTPConfig) -> None:
        super().__init__()
        self._email_sender = email_sender
        self._config = config
        self.signals = _TestEmailSignals()

    def run(self) -> None:
        try:
            self._email_sender.send_test(self._config)
        except _EMAIL_ERRORS:
            logger.exception("Could not send SMTP test message.")
            self.signals.finished.emit(False)
            return
        self.signals.finished.emit(True)


class SettingsPanel(QWidget):
    def __init__(
        self,
        database: Database,
        credential_store: CredentialStore,
        settings: AppSettings,
        on_saved: Callable[[AppSettings], None],
        parent: QWidget | None = None,
        email_sender: EmailSender | None = None,
    ) -> None:
        super().__init__(parent)
        self._database = database
        self._credential_store = credential_store
        self._on_saved = on_saved
        self._settings = settings
        self._email_sender = (
            EmailSender(credential_store) if email_sender is None else email_sender
        )
        self._email_test_worker: _TestEmailWorker | None = None
        self.setObjectName("settingsPanel")
        self.setStyleSheet(
            """
            QWidget#settingsPanel { background: #f3f6fa; color: #172338; }
            QLabel#pageTitle { font-size: 25px; font-weight: 700; }
            QLabel#subtitle, QLabel#status, QLabel.note { color: #718096; }
            QFrame#settingsCard {
                background: #ffffff;
                border: 1px solid #e3e9f1;
                border-radius: 12px;
            }
            QLabel.sectionTitle { font-size: 16px; font-weight: 650; }
            QLineEdit, QSpinBox, QTimeEdit, QComboBox {
                border: 1px solid #d5deea;
                border-radius: 6px;
                padding: 6px 8px;
                background: #ffffff;
            }
            QPushButton#saveButton {
                color: #ffffff;
                background: #3867d6;
                border: 0;
                border-radius: 7px;
                padding: 9px 18px;
                font-weight: 600;
            }
            QPushButton#saveButton:hover { background: #2e58bd; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(15)

        title = QLabel("Ustawienia")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Konfiguracja monitorowania i raportów")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        monitoring_card, monitoring_form = self._form_card("Monitorowanie")
        self.idle_timeout_minutes = QSpinBox()
        self.idle_timeout_minutes.setRange(1, 1440)
        self.idle_timeout_minutes.setSuffix(" min")
        self.idle_timeout_minutes.setValue(
            max(1, round(settings.idle_timeout_seconds / 60))
        )
        self.ignored_processes = QLineEdit(", ".join(settings.ignored_processes))
        self.ignored_processes.setPlaceholderText("np. game.exe, example.exe")
        monitoring_form.addRow("Czas bezczynności", self.idle_timeout_minutes)
        monitoring_form.addRow("Ignorowane procesy", self.ignored_processes)
        layout.addWidget(monitoring_card)

        report_card, report_form = self._form_card("Raporty e-mail")
        self.auto_reports_enabled = QCheckBox(
            "Włącz automatyczną wysyłkę raportów"
        )
        self.auto_reports_enabled.setChecked(settings.auto_reports_enabled)
        report_form.addRow("", self.auto_reports_enabled)

        self.smtp_host = QLineEdit(settings.smtp_host)
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(settings.smtp_port)
        self.smtp_sender = QLineEdit(settings.smtp_sender)
        self.smtp_recipient = QLineEdit(settings.smtp_recipient)
        self.smtp_username = QLineEdit(settings.smtp_username)
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.smtp_password.setPlaceholderText(
            "Wpisz, aby dodać lub zmienić hasło"
        )
        self.smtp_security = QComboBox()
        self.smtp_security.addItem("STARTTLS", "starttls")
        self.smtp_security.addItem("SSL", "ssl")
        self.smtp_security.setCurrentIndex(
            max(0, self.smtp_security.findData(settings.smtp_security_mode))
        )
        report_form.addRow("Serwer SMTP", self.smtp_host)
        report_form.addRow("Port", self.smtp_port)
        report_form.addRow("Zabezpieczenie połączenia", self.smtp_security)
        report_form.addRow("Adres nadawcy", self.smtp_sender)
        report_form.addRow("Adres odbiorcy", self.smtp_recipient)
        report_form.addRow("Użytkownik SMTP", self.smtp_username)
        report_form.addRow("Hasło SMTP", self.smtp_password)
        note = QLabel(
            "Hasło jest przechowywane w Windows Credential Manager, poza bazą ustawień."
        )
        note.setProperty("class", "note")
        note.setWordWrap(True)
        report_form.addRow("", note)

        self.daily_report_time = QTimeEdit()
        self.daily_report_time.setDisplayFormat("HH:mm")
        self.daily_report_time.setTime(
            QTime(
                settings.daily_report_time.hour,
                settings.daily_report_time.minute,
            )
        )
        self.weekly_report_day = QComboBox()
        for day_index, day_name in enumerate(_WEEKDAYS):
            self.weekly_report_day.addItem(day_name, day_index)
        self.weekly_report_day.setCurrentIndex(settings.weekly_report_day)
        self.weekly_report_time = QTimeEdit()
        self.weekly_report_time.setDisplayFormat("HH:mm")
        self.weekly_report_time.setTime(
            QTime(
                settings.weekly_report_time.hour,
                settings.weekly_report_time.minute,
            )
        )
        report_form.addRow("Godzina raportu dziennego", self.daily_report_time)
        report_form.addRow("Dzień raportu tygodniowego", self.weekly_report_day)
        report_form.addRow("Godzina raportu tygodniowego", self.weekly_report_time)
        layout.addWidget(report_card)
        layout.addStretch(1)

        footer = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.save_button = QPushButton("Zapisz ustawienia")
        self.save_button.setObjectName("saveButton")
        self.save_button.clicked.connect(self.save)
        self.test_email_button = QPushButton("Wyślij e-mail testowy")
        self.test_email_button.clicked.connect(self.send_test_email)
        footer.addWidget(self.status_label, stretch=1)
        footer.addWidget(self.test_email_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        scroll.setWidget(content)
        root.addWidget(scroll)

    def send_test_email(self) -> None:
        try:
            username = self.smtp_username.text().strip()
            password = self.smtp_password.text()
            if password and not username:
                raise ValueError("SMTP username is required when setting a password.")
            config = SMTPConfig(
                host=self.smtp_host.text().strip(),
                port=self.smtp_port.value(),
                sender=self.smtp_sender.text().strip(),
                recipient=self.smtp_recipient.text().strip(),
                username=username,
                security_mode=str(self.smtp_security.currentData()),
            )
            if password:
                self._credential_store.set_password(username, password)
                self.smtp_password.clear()

            worker = _TestEmailWorker(self._email_sender, config)
            worker.signals.finished.connect(self._on_test_email_finished)
            self._email_test_worker = worker
            self.test_email_button.setEnabled(False)
            self.status_label.setText("Wysyłanie wiadomości testowej…")
            QThreadPool.globalInstance().start(worker)
        except _EMAIL_ERRORS:
            logger.exception("Could not start SMTP test message.")
            self.status_label.setText("Nie udało się wysłać wiadomości testowej.")

    def _on_test_email_finished(self, succeeded: bool) -> None:
        self._email_test_worker = None
        self.test_email_button.setEnabled(True)
        self.status_label.setText(
            "Wiadomość testowa została wysłana."
            if succeeded
            else "Nie udało się wysłać wiadomości testowej."
        )

    @staticmethod
    def _form_card(title: str) -> tuple[QFrame, QFormLayout]:
        card = QFrame()
        card.setObjectName("settingsCard")
        layout = QFormLayout(card)
        layout.setContentsMargins(16, 13, 16, 15)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(10)
        heading = QLabel(title)
        heading.setProperty("class", "sectionTitle")
        layout.addRow(heading)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return card, layout

    def save(self) -> None:
        try:
            ignored = tuple(
                dict.fromkeys(
                    name.strip()
                    for name in self.ignored_processes.text().split(",")
                    if name.strip()
                )
            )
            settings = AppSettings(
                idle_timeout_seconds=self.idle_timeout_minutes.value() * 60,
                ignored_processes=ignored,
                smtp_host=self.smtp_host.text().strip(),
                smtp_port=self.smtp_port.value(),
                smtp_sender=self.smtp_sender.text().strip(),
                smtp_recipient=self.smtp_recipient.text().strip(),
                smtp_username=self.smtp_username.text().strip(),
                smtp_security_mode=str(self.smtp_security.currentData()),
                auto_reports_enabled=self.auto_reports_enabled.isChecked(),
                daily_report_time=self.daily_report_time.time().toPython(),
                weekly_report_day=int(self.weekly_report_day.currentData()),
                weekly_report_time=self.weekly_report_time.time().toPython(),
            )
            password = self.smtp_password.text()
            if password and not settings.smtp_username:
                raise ValueError("SMTP username is required when setting a password.")
            if password:
                self._credential_store.set_password(
                    settings.smtp_username, password
                )
            settings.save(self._database)
            self._settings = settings
            self.smtp_password.clear()
            self._on_saved(settings)
            self.status_label.setText("Ustawienia zapisano.")
        except (ValueError, sqlite3.Error, OSError, keyring.errors.KeyringError):
            logger.exception("Could not save ActivityTracker settings.")
            self.status_label.setText("Nie udało się zapisać ustawień.")
