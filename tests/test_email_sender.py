import smtplib

import pytest

from reports.email_sender import (
    CredentialStore,
    EmailSender,
    MissingCredentialError,
    SMTPConfig,
)
from reports.models import GeneratedReport


_PNG = b"\x89PNG\r\n\x1a\nexample"


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def get_password(self, service, username):
        return self.values.get((service, username))

    def delete_password(self, service, username):
        del self.values[(service, username)]


class MemoryCredentials:
    def get_password(self, username):
        if username != "account@example.com":
            raise MissingCredentialError("Missing test credential.")
        return "test-secret"


class FakeSMTP:
    instances = []

    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.calls = []
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self, *, context):
        self.calls.append(("starttls", context))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.message = message
        self.calls.append(("send_message",))
        return {}


def _report():
    return GeneratedReport(
        subject="ActivityTracker daily",
        html='<html><img src="cid:daily-chart"></html>',
        chart_png=_PNG,
        chart_cid="daily-chart",
    )


def _config(**overrides):
    values = {
        "host": "smtp.example.com",
        "port": 587,
        "sender": "tracker@example.com",
        "recipient": "user@example.com",
        "username": "account@example.com",
    }
    values.update(overrides)
    return SMTPConfig(**values)


def test_credential_store_uses_injected_keyring_backend():
    backend = MemoryKeyring()
    credentials = CredentialStore(backend=backend)
    credentials.set_password("account@example.com", "private-password")

    assert credentials.get_password("account@example.com") == "private-password"
    credentials.delete_password("account@example.com")
    with pytest.raises(MissingCredentialError):
        credentials.get_password("account@example.com")


def test_sender_uses_starttls_auth_and_embeds_chart(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("reports.email_sender.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr(
        "reports.email_sender.ssl.create_default_context", lambda: "tls-context"
    )
    sender = EmailSender(MemoryCredentials())

    sender.send(_report(), _config())

    client = FakeSMTP.instances[0]
    assert client.host == "smtp.example.com"
    assert client.calls[0] == ("ehlo",)
    assert client.calls[1] == ("starttls", "tls-context")
    assert client.calls[3] == ("login", "account@example.com", "test-secret")
    message = client.message
    assert message["To"] == "user@example.com"
    image = next(part for part in message.walk() if part.get_content_type() == "image/png")
    assert image.get_payload(decode=True) == _PNG
    assert image["Content-ID"] == "<daily-chart>"
    assert image.get_content_disposition() == "inline"
    assert "test-secret" not in message.as_string()


def test_sender_uses_implicit_tls_for_ssl_mode(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("reports.email_sender.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(
        "reports.email_sender.ssl.create_default_context", lambda: "tls-context"
    )

    EmailSender(MemoryCredentials()).send(
        _report(), _config(port=465, security_mode="ssl")
    )

    client = FakeSMTP.instances[0]
    assert client.kwargs["context"] == "tls-context"
    assert ("send_message",) in client.calls


def test_sender_sends_a_plain_test_email_without_a_report_attachment(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("reports.email_sender.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr(
        "reports.email_sender.ssl.create_default_context", lambda: "tls-context"
    )

    EmailSender(MemoryCredentials()).send_test(_config())

    message = FakeSMTP.instances[0].message
    assert message["Subject"] == "ActivityTracker — test konfiguracji SMTP"
    assert message["To"] == "user@example.com"
    assert "testowa wiadomość" in message.get_body(preferencelist=("plain",)).get_content()
    assert not any(part.get_content_type() == "image/png" for part in message.walk())


def test_missing_smtp_credential_fails_before_network_connection(monkeypatch):
    class MissingCredentials:
        def get_password(self, username):
            raise MissingCredentialError("Missing test credential.")

    def unexpected_connection(*args, **kwargs):
        pytest.fail("SMTP must not be opened without credentials.")

    monkeypatch.setattr("reports.email_sender.smtplib.SMTP", unexpected_connection)

    with pytest.raises(MissingCredentialError):
        EmailSender(MissingCredentials()).send(_report(), _config())


@pytest.mark.parametrize(
    "overrides",
    [
        {"port": 0},
        {"security_mode": "none"},
        {"recipient": "bad-address"},
        {"sender": "sender@example.com\r\nBcc: other@example.com"},
    ],
)
def test_smtp_configuration_rejects_invalid_values(overrides):
    with pytest.raises(ValueError):
        _config(**overrides)


def test_sender_rejects_invalid_chart_and_refused_recipient(monkeypatch):
    invalid_report = GeneratedReport(
        subject="bad",
        html="<p>report</p>",
        chart_png=b"not a png",
        chart_cid="chart",
    )
    with pytest.raises(ValueError, match="valid PNG"):
        EmailSender(MemoryCredentials()).send(invalid_report, _config())

    class RefusingSMTP(FakeSMTP):
        def send_message(self, message):
            return {"user@example.com": (550, b"mailbox unavailable")}

    monkeypatch.setattr("reports.email_sender.smtplib.SMTP", RefusingSMTP)
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        EmailSender(MemoryCredentials()).send(_report(), _config())
