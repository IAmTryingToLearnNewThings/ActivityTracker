"""Secure SMTP delivery for generated ActivityTracker reports."""

from __future__ import annotations

import math
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr

import keyring

from reports.models import GeneratedReport

_CREDENTIAL_SERVICE = "ActivityTracker SMTP"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CONTENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+\Z")


class MissingCredentialError(RuntimeError):
    """Raised when an SMTP username has no password in the credential store."""


class CredentialStore:
    """Store SMTP credentials in Windows Credential Manager via keyring."""

    def __init__(self, backend=None) -> None:
        if backend is None:
            if os.name != "nt":
                raise OSError("SMTP credentials require Windows Credential Manager.")
            from keyring.backends.Windows import WinVaultKeyring

            backend = keyring.get_keyring()
            if not isinstance(backend, WinVaultKeyring):
                raise RuntimeError(
                    "The active keyring backend is not Windows Credential Manager."
                )
        self._backend = backend

    def set_password(self, username: str, password: str) -> None:
        if not username.strip():
            raise ValueError("SMTP username must not be empty.")
        if not password:
            raise ValueError("SMTP password must not be empty.")
        self._backend.set_password(_CREDENTIAL_SERVICE, username, password)

    def get_password(self, username: str) -> str:
        if not username.strip():
            raise ValueError("SMTP username must not be empty.")
        password = self._backend.get_password(_CREDENTIAL_SERVICE, username)
        if password is None:
            raise MissingCredentialError(
                "No SMTP credential is saved for the configured username."
            )
        return password

    def delete_password(self, username: str) -> None:
        if not username.strip():
            raise ValueError("SMTP username must not be empty.")
        self._backend.delete_password(_CREDENTIAL_SERVICE, username)


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    sender: str
    recipient: str
    username: str = ""
    security_mode: str = "starttls"
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not self.host.strip() or any(char.isspace() for char in self.host):
            raise ValueError("SMTP host must be a non-empty hostname.")
        if not 1 <= self.port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535.")
        if self.security_mode not in {"starttls", "ssl"}:
            raise ValueError("SMTP security mode must be 'starttls' or 'ssl'.")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("SMTP timeout must be a finite positive value.")
        self._validate_address(self.sender, "sender")
        self._validate_address(self.recipient, "recipient")

    @staticmethod
    def _validate_address(value: str, label: str) -> None:
        if "\r" in value or "\n" in value:
            raise ValueError(f"SMTP {label} address must not contain newlines.")
        _, address = parseaddr(value)
        if not address or "@" not in address:
            raise ValueError(f"SMTP {label} must be a valid email address.")


class EmailSender:
    """Build and send report messages using TLS-protected SMTP."""

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self._credential_store = (
            CredentialStore() if credential_store is None else credential_store
        )

    def send(self, report: GeneratedReport, config: SMTPConfig) -> None:
        message = self._create_message(report, config)
        self._send_message(message, config)

    def send_test(self, config: SMTPConfig) -> None:
        """Send a small plain HTML message to verify the configured SMTP path."""
        message = EmailMessage()
        message["Subject"] = "ActivityTracker — test konfiguracji SMTP"
        message["From"] = config.sender
        message["To"] = config.recipient
        message.set_content(
            "To jest testowa wiadomość z ActivityTracker. "
            "Konfiguracja poczty działa poprawnie."
        )
        message.add_alternative(
            "<html><body><p>To jest testowa wiadomość z ActivityTracker.</p>"
            "<p>Konfiguracja poczty działa poprawnie.</p></body></html>",
            subtype="html",
        )
        self._send_message(message, config)

    def _send_message(self, message: EmailMessage, config: SMTPConfig) -> None:
        password = (
            self._credential_store.get_password(config.username)
            if config.username
            else None
        )
        context = ssl.create_default_context()

        if config.security_mode == "ssl":
            with smtplib.SMTP_SSL(
                config.host,
                config.port,
                timeout=config.timeout_seconds,
                context=context,
            ) as client:
                self._deliver(client, message, config, password)
        else:
            with smtplib.SMTP(
                config.host,
                config.port,
                timeout=config.timeout_seconds,
            ) as client:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
                self._deliver(client, message, config, password)

    @staticmethod
    def _create_message(
        report: GeneratedReport, config: SMTPConfig
    ) -> EmailMessage:
        if not report.chart_png.startswith(_PNG_SIGNATURE):
            raise ValueError("Report chart is not a valid PNG image.")
        if not _CONTENT_ID_PATTERN.fullmatch(report.chart_cid):
            raise ValueError("Report chart content ID is invalid.")

        message = EmailMessage()
        message["Subject"] = report.subject
        message["From"] = config.sender
        message["To"] = config.recipient
        message.set_content(
            "Ten raport ActivityTracker zawiera wersję HTML z wykresem. "
            "Otwórz wiadomość w kliencie obsługującym HTML."
        )
        message.add_alternative(report.html, subtype="html")
        html_part = message.get_body(preferencelist=("html",))
        if html_part is None:
            raise RuntimeError("Could not construct the HTML email body.")
        html_part.add_related(
            report.chart_png,
            maintype="image",
            subtype="png",
            cid=f"<{report.chart_cid}>",
            filename="activity-report.png",
            disposition="inline",
        )
        return message

    @staticmethod
    def _deliver(
        client: smtplib.SMTP,
        message: EmailMessage,
        config: SMTPConfig,
        password: str | None,
    ) -> None:
        if config.username:
            if password is None:
                raise MissingCredentialError(
                    "SMTP authentication requires a saved credential."
                )
            client.login(config.username, password)
        refused = client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
