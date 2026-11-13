"""Background scheduling for daily and weekly report delivery."""

from __future__ import annotations

import keyring
import logging
import math
import smtplib
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from config.settings import AppSettings
from core.database import Database
from reports.daily_report import DailyReport
from reports.email_sender import EmailSender, MissingCredentialError
from reports.weekly_report import WeeklyReport

logger = logging.getLogger(__name__)

_DELIVERY_ERRORS = (
    OSError,
    sqlite3.Error,
    smtplib.SMTPException,
    ValueError,
    MissingCredentialError,
    keyring.errors.KeyringError,
)


class ReportScheduler:
    """Poll local schedules and deliver each completed period at most once."""

    def __init__(
        self,
        database: Database,
        daily_report: DailyReport,
        weekly_report: WeeklyReport,
        email_sender: EmailSender,
        settings_provider: Callable[[], AppSettings],
        *,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
            raise ValueError("Scheduler interval must be a finite positive value.")
        self._database = database
        self._daily_report = daily_report
        self._weekly_report = weekly_report
        self._email_sender = email_sender
        self._settings_provider = settings_provider
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._pending: set[tuple[str, date, date]] = set()
        self._futures: set[Future[None]] = set()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ActivityTrackerReports"
        )
        self._thread: threading.Thread | None = None
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("A stopped report scheduler cannot be restarted.")
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Report scheduler is already running.")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ActivityTrackerScheduler", daemon=True
        )
        self._thread.start()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("Report scheduler did not stop in time.")
            self._thread = None
        self._executor.shutdown(wait=True)
        self._closed = True

    def check_once(self, now: datetime | None = None) -> None:
        current = datetime.now().astimezone() if now is None else now
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Scheduler time must be timezone-aware.")
        local_now = current.astimezone()
        settings = self._settings_provider()
        if not settings.auto_reports_enabled:
            return

        if local_now.time().replace(tzinfo=None) >= settings.daily_report_time:
            report_day = local_now.date() - timedelta(days=1)
            self._submit("daily", report_day, report_day, settings)

        if (
            local_now.weekday() == settings.weekly_report_day
            and local_now.time().replace(tzinfo=None) >= settings.weekly_report_time
        ):
            current_week_start = local_now.date() - timedelta(
                days=local_now.weekday()
            )
            previous_week_start = current_week_start - timedelta(days=7)
            previous_week_end = previous_week_start + timedelta(days=6)
            self._submit(
                "weekly", previous_week_start, previous_week_end, settings
            )

    def wait_for_idle(self, timeout: float = 30.0) -> bool:
        with self._lock:
            futures = tuple(self._futures)
        if not futures:
            return True
        _, unfinished = wait(futures, timeout=timeout)
        return not unfinished

    def _submit(
        self,
        report_type: str,
        period_start: date,
        period_end: date,
        settings: AppSettings,
    ) -> None:
        key = (report_type, period_start, period_end)
        if self._database.has_report_attempt(report_type, period_start, period_end):
            return
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            future = self._executor.submit(self._deliver, key, settings)
            self._futures.add(future)
            future.add_done_callback(
                lambda completed, item=key: self._on_done(item, completed)
            )

    def _deliver(
        self, key: tuple[str, date, date], settings: AppSettings
    ) -> None:
        report_type, period_start, period_end = key
        attempted_at = datetime.now().astimezone()
        try:
            if report_type == "daily":
                report = self._daily_report.generate(period_start)
            else:
                report = self._weekly_report.generate(period_start)
            self._email_sender.send(report, settings.to_smtp_config())
        except _DELIVERY_ERRORS as exc:
            logger.exception("Scheduled %s report delivery failed.", report_type)
            self._database.record_report_attempt(
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
                recipient=settings.smtp_recipient,
                status="failed",
                attempted_at=attempted_at,
                error_message=type(exc).__name__,
            )
            return

        self._database.record_report_attempt(
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            recipient=settings.smtp_recipient,
            status="sent",
            attempted_at=attempted_at,
            sent_at=datetime.now(timezone.utc),
        )

    def _on_done(
        self, key: tuple[str, date, date], future: Future[None]
    ) -> None:
        with self._lock:
            self._pending.discard(key)
            self._futures.discard(future)
        try:
            future.result()
        except Exception:
            logger.exception("Scheduled report worker failed unexpectedly.")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except (OSError, sqlite3.Error, ValueError):
                logger.exception("Could not evaluate report schedule.")
            self._stop_event.wait(self._poll_interval_seconds)
