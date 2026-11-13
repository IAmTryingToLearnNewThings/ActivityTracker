"""Typed application settings persisted in SQLite."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import time

from core.database import Database
from reports.email_sender import SMTPConfig


@dataclass(frozen=True)
class AppSettings:
    idle_timeout_seconds: float = 300.0
    ignored_processes: tuple[str, ...] = ()
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_sender: str = ""
    smtp_recipient: str = ""
    smtp_username: str = ""
    smtp_security_mode: str = "starttls"
    auto_reports_enabled: bool = False
    daily_report_time: time = time(20, 0)
    weekly_report_day: int = 0
    weekly_report_time: time = time(9, 0)

    def __post_init__(self) -> None:
        if not math.isfinite(self.idle_timeout_seconds) or self.idle_timeout_seconds <= 0:
            raise ValueError("Idle timeout must be a finite positive value.")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535.")
        if self.smtp_security_mode not in {"starttls", "ssl"}:
            raise ValueError("SMTP security mode must be 'starttls' or 'ssl'.")
        if not 0 <= self.weekly_report_day <= 6:
            raise ValueError("Weekly report day must be between 0 and 6.")
        if self.daily_report_time.tzinfo is not None:
            raise ValueError("Daily report time must be a local time.")
        if self.weekly_report_time.tzinfo is not None:
            raise ValueError("Weekly report time must be a local time.")
        if any(not process.strip() for process in self.ignored_processes):
            raise ValueError("Ignored process names must not be empty.")
        if self.auto_reports_enabled and not all(
            (
                self.smtp_host.strip(),
                self.smtp_sender.strip(),
                self.smtp_recipient.strip(),
            )
        ):
            raise ValueError(
                "SMTP host, sender, and recipient are required for automatic reports."
            )

    @classmethod
    def load(cls, database: Database) -> AppSettings:
        def setting(key: str, default: str) -> str:
            value = database.get_setting(key, default)
            if value is None:
                raise ValueError(f"Setting {key!r} has no value.")
            return value

        enabled_text = setting("auto_reports_enabled", "false").casefold()
        if enabled_text not in {"true", "false"}:
            raise ValueError("Automatic report setting must be true or false.")

        raw_ignored = setting("ignored_processes", "[]")
        try:
            parsed_ignored = json.loads(raw_ignored)
        except json.JSONDecodeError:
            parsed_ignored = [name.strip() for name in raw_ignored.split(",")]
        if not isinstance(parsed_ignored, list) or not all(
            isinstance(name, str) for name in parsed_ignored
        ):
            raise ValueError("Ignored process setting must be a list of names.")

        try:
            return cls(
                idle_timeout_seconds=float(
                    setting("idle_timeout_seconds", "300")
                ),
                ignored_processes=tuple(
                    name.strip() for name in parsed_ignored if name.strip()
                ),
                smtp_host=setting("smtp_host", ""),
                smtp_port=int(setting("smtp_port", "587")),
                smtp_sender=setting("smtp_sender", ""),
                smtp_recipient=setting("smtp_recipient", ""),
                smtp_username=setting("smtp_username", ""),
                smtp_security_mode=setting("smtp_security_mode", "starttls"),
                auto_reports_enabled=enabled_text == "true",
                daily_report_time=time.fromisoformat(
                    setting("daily_report_time", "20:00")
                ),
                weekly_report_day=int(setting("weekly_report_day", "0")),
                weekly_report_time=time.fromisoformat(
                    setting("weekly_report_time", "09:00")
                ),
            )
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("Stored application settings are invalid.") from exc

    def save(self, database: Database) -> None:
        database.set_settings(
            {
                "idle_timeout_seconds": str(self.idle_timeout_seconds),
                "ignored_processes": json.dumps(
                    self.ignored_processes, ensure_ascii=False
                ),
                "smtp_host": self.smtp_host.strip(),
                "smtp_port": str(self.smtp_port),
                "smtp_sender": self.smtp_sender.strip(),
                "smtp_recipient": self.smtp_recipient.strip(),
                "smtp_username": self.smtp_username.strip(),
                "smtp_security_mode": self.smtp_security_mode,
                "auto_reports_enabled": str(self.auto_reports_enabled).lower(),
                "daily_report_time": self.daily_report_time.strftime("%H:%M"),
                "weekly_report_day": str(self.weekly_report_day),
                "weekly_report_time": self.weekly_report_time.strftime("%H:%M"),
            }
        )

    def to_smtp_config(self) -> SMTPConfig:
        return SMTPConfig(
            host=self.smtp_host,
            port=self.smtp_port,
            sender=self.smtp_sender,
            recipient=self.smtp_recipient,
            username=self.smtp_username,
            security_mode=self.smtp_security_mode,
        )
