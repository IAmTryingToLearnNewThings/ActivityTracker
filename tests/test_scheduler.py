import smtplib
import threading
from datetime import date, datetime, time

import pytest

from config.settings import AppSettings
from core.database import Database
from core.scheduler import ReportScheduler
from reports.models import GeneratedReport


@pytest.fixture
def database():
    instance = Database(":memory:")
    try:
        yield instance
    finally:
        instance.close()


class FakeDailyReport:
    def __init__(self):
        self.dates = []

    def generate(self, report_date):
        self.dates.append(report_date)
        return GeneratedReport("Daily", "<p>Daily</p>", b"", "daily")


class FakeWeeklyReport:
    def __init__(self):
        self.weeks = []

    def generate(self, week_of):
        self.weeks.append(week_of)
        return GeneratedReport("Weekly", "<p>Weekly</p>", b"", "weekly")


class FakeEmailSender:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def send(self, report, config):
        if self.error is not None:
            raise self.error
        self.sent.append((report, config))


def _settings(**overrides):
    values = {
        "smtp_host": "smtp.example.com",
        "smtp_sender": "tracker@example.com",
        "smtp_recipient": "user@example.com",
        "auto_reports_enabled": True,
        "daily_report_time": time(8, 0),
        "weekly_report_day": 0,
        "weekly_report_time": time(9, 0),
    }
    values.update(overrides)
    return AppSettings(**values)


def _scheduler(database, daily, weekly, sender, settings):
    return ReportScheduler(
        database,
        daily,
        weekly,
        sender,
        settings_provider=lambda: settings,
        poll_interval_seconds=60,
    )


def test_scheduler_sends_configured_local_day_once(database):
    daily = FakeDailyReport()
    weekly = FakeWeeklyReport()
    sender = FakeEmailSender()
    scheduler = _scheduler(database, daily, weekly, sender, _settings())
    now = datetime.combine(date(2026, 11, 13), time(10)).astimezone()
    try:
        scheduler.check_once(now)
        assert scheduler.wait_for_idle()
        scheduler.check_once(now)
        assert scheduler.wait_for_idle()

        assert daily.dates == [date(2026, 11, 13)]
        assert len(sender.sent) == 1
        history = database.get_report_history()
        assert len(history) == 1
        assert history[0].period_start == date(2026, 11, 13)
        assert history[0].status == "sent"
    finally:
        scheduler.stop()


def test_scheduler_sends_november_13_report_at_2359(database):
    daily = FakeDailyReport()
    scheduler = _scheduler(
        database,
        daily,
        FakeWeeklyReport(),
        FakeEmailSender(),
        _settings(daily_report_time=time(23, 59)),
    )
    scheduled_time = datetime.combine(
        date(2026, 11, 13), time(23, 59)
    ).astimezone()
    try:
        scheduler.check_once(scheduled_time)
        assert scheduler.wait_for_idle()

        assert daily.dates == [date(2026, 11, 13)]
        assert database.get_report_history()[0].period_start == date(2026, 11, 13)
    finally:
        scheduler.stop()


def test_scheduler_catches_up_for_previous_day_after_midnight(database):
    daily = FakeDailyReport()
    scheduler = _scheduler(
        database,
        daily,
        FakeWeeklyReport(),
        FakeEmailSender(),
        _settings(daily_report_time=time(23, 59)),
    )
    after_midnight = datetime.combine(
        date(2026, 11, 14), time(0, 1)
    ).astimezone()
    try:
        scheduler.check_once(after_midnight)
        assert scheduler.wait_for_idle()

        assert daily.dates == [date(2026, 11, 13)]
    finally:
        scheduler.stop()


def test_midnight_report_time_sends_the_previous_complete_day(database):
    daily = FakeDailyReport()
    scheduler = _scheduler(
        database,
        daily,
        FakeWeeklyReport(),
        FakeEmailSender(),
        _settings(daily_report_time=time.min),
    )
    midnight = datetime.combine(date(2026, 11, 14), time.min).astimezone()
    try:
        scheduler.check_once(midnight)
        assert scheduler.wait_for_idle()

        assert daily.dates == [date(2026, 11, 13)]
    finally:
        scheduler.stop()


def test_scheduler_sends_last_complete_week_on_configured_weekday(database):
    daily = FakeDailyReport()
    weekly = FakeWeeklyReport()
    sender = FakeEmailSender()
    scheduler = _scheduler(
        database,
        daily,
        weekly,
        sender,
        _settings(daily_report_time=time(23, 59)),
    )
    monday = datetime.combine(date(2026, 11, 16), time(10)).astimezone()
    try:
        scheduler.check_once(monday)
        assert scheduler.wait_for_idle()

        assert weekly.weeks == [date(2026, 11, 9)]
        history = database.get_report_history()
        weekly_history = next(item for item in history if item.report_type == "weekly")
        assert weekly_history.period_start == date(2026, 11, 9)
        assert weekly_history.period_end == date(2026, 11, 15)
    finally:
        scheduler.stop()


def test_scheduler_records_failed_delivery_without_storing_error_details(database):
    daily = FakeDailyReport()
    weekly = FakeWeeklyReport()
    sender = FakeEmailSender(smtplib.SMTPException("private response"))
    scheduler = _scheduler(database, daily, weekly, sender, _settings())
    now = datetime.combine(date(2026, 11, 13), time(10)).astimezone()
    try:
        scheduler.check_once(now)
        assert scheduler.wait_for_idle()
        scheduler.check_once(now)
        assert scheduler.wait_for_idle()

        history = database.get_report_history()
        assert len(history) == 1
        assert history[0].status == "failed"
        assert history[0].error_message == "SMTPException"
        assert "private response" not in history[0].error_message
    finally:
        scheduler.stop()


def test_scheduler_does_nothing_when_auto_reports_are_disabled(database):
    daily = FakeDailyReport()
    weekly = FakeWeeklyReport()
    sender = FakeEmailSender()
    scheduler = _scheduler(
        database, daily, weekly, sender, _settings(auto_reports_enabled=False)
    )
    try:
        scheduler.check_once(
            datetime.combine(date(2026, 11, 13), time(10)).astimezone()
        )
        assert scheduler.wait_for_idle()
        assert daily.dates == []
        assert weekly.weeks == []
        assert sender.sent == []
    finally:
        scheduler.stop()


def test_scheduler_dispatches_email_without_blocking_schedule_check(database):
    started = threading.Event()
    release = threading.Event()

    class SlowSender(FakeEmailSender):
        def send(self, report, config):
            started.set()
            release.wait(timeout=2)
            self.sent.append((report, config))

    sender = SlowSender()
    scheduler = _scheduler(
        database,
        FakeDailyReport(),
        FakeWeeklyReport(),
        sender,
        _settings(),
    )
    now = datetime.combine(date(2026, 11, 13), time(10)).astimezone()
    try:
        scheduler.check_once(now)
        assert started.wait(timeout=1)
        assert not scheduler.wait_for_idle(timeout=0.01)
        release.set()
        assert scheduler.wait_for_idle(timeout=1)
    finally:
        release.set()
        scheduler.stop()
