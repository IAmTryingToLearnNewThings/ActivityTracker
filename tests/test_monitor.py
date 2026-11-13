import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from core.database import Database
from core.idle_detector import IdleDetector
from core.monitor import ActivityMonitor
from core.process_manager import ProcessIdentity


class FakeProcessManager:
    def __init__(self):
        self.process = None

    def current_process(self):
        return self.process


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0
        self.current_time = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.elapsed += seconds
        self.current_time += timedelta(seconds=seconds)

    def monotonic(self):
        return self.elapsed

    def now(self):
        return self.current_time


@pytest.fixture
def database():
    instance = Database(":memory:")
    try:
        yield instance
    finally:
        instance.close()


def test_monitor_accounts_foreground_transitions(database):
    clock = FakeClock()
    processes = FakeProcessManager()
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
    )
    editor = ProcessIdentity("Code", "code.exe")
    browser = ProcessIdentity("Firefox", "firefox.exe")

    processes.process = editor
    monitor.sample_once()
    clock.advance(2)
    monitor.sample_once()

    processes.process = browser
    clock.advance(1)
    monitor.sample_once()
    clock.advance(3)
    monitor.sample_once()

    processes.process = None
    clock.advance(1)
    monitor.sample_once()

    totals = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    assert {
        item.process_name: item.duration_seconds for item in totals
    } == {"code.exe": 3, "firefox.exe": 4}


def test_monitor_stop_persists_final_interval(database):
    clock = FakeClock()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
    )

    monitor.sample_once()
    clock.advance(5)
    monitor.stop()

    totals = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    assert totals[0].duration_seconds == 5


def test_monitor_can_pause_accounting_through_eligibility_callback(database):
    clock = FakeClock()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    allowed = True
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
        is_tracking_allowed=lambda: allowed,
    )

    monitor.sample_once()
    clock.advance(2)
    allowed = False
    monitor.sample_once()
    clock.advance(10)
    allowed = True
    monitor.sample_once()
    clock.advance(3)
    monitor.stop()

    totals = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    assert totals[0].duration_seconds == 5


def test_monitor_splits_sessions_at_local_midnight(database):
    clock = FakeClock()
    clock.current_time = datetime(2026, 11, 12, 23, 59, 59).astimezone()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
    )

    monitor.sample_once()
    clock.advance(2)
    monitor.sample_once()
    clock.advance(2)
    monitor.stop()

    previous_day = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    next_day = database.get_usage_totals(
        date(2026, 11, 13), date(2026, 11, 13)
    )
    assert previous_day[0].duration_seconds == 1
    assert next_day[0].duration_seconds == 3


def test_monitor_splits_at_midnight_when_process_changes(database):
    clock = FakeClock()
    clock.current_time = datetime(2026, 11, 12, 23, 59, 59).astimezone()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
    )

    monitor.sample_once()
    clock.advance(2)
    processes.process = ProcessIdentity("Firefox", "firefox.exe")
    monitor.sample_once()
    clock.advance(2)
    monitor.stop()

    previous_day = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    next_day = database.get_usage_totals(
        date(2026, 11, 13), date(2026, 11, 13)
    )
    assert previous_day[0].process_name == "code.exe"
    assert previous_day[0].duration_seconds == 1
    assert {
        item.process_name: item.duration_seconds for item in next_day
    } == {"code.exe": 1, "firefox.exe": 2}


def test_idle_and_lock_state_prevent_time_accounting(database):
    clock = FakeClock()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    idle_seconds = 0
    locked = False
    detector = IdleDetector(
        idle_duration_provider=lambda: idle_seconds,
        locked_provider=lambda: locked,
    )
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
        is_tracking_allowed=lambda: detector.is_tracking_allowed(300),
    )

    monitor.sample_once()
    clock.advance(2)
    idle_seconds = 300
    monitor.sample_once()
    clock.advance(10)
    idle_seconds = 0
    locked = True
    monitor.sample_once()
    clock.advance(20)
    locked = False
    monitor.sample_once()
    clock.advance(3)
    monitor.stop()

    totals = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    assert [item.duration_seconds for item in totals] == [5]


def test_pause_and_resume_exclude_paused_time(database):
    clock = FakeClock()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
    )

    monitor.sample_once()
    clock.advance(2)
    monitor.pause()
    assert monitor.is_paused
    clock.advance(10)
    monitor.sample_once()
    monitor.resume()
    assert not monitor.is_paused
    monitor.sample_once()
    clock.advance(3)
    monitor.stop()

    totals = database.get_usage_totals(
        date(2026, 11, 12), date(2026, 11, 12)
    )
    assert totals[0].duration_seconds == 5


def test_pause_failure_does_not_report_monitor_as_paused(database, monkeypatch):
    clock = FakeClock()
    processes = FakeProcessManager()
    processes.process = ProcessIdentity("Code", "code.exe")
    monitor = ActivityMonitor(
        database,
        processes,
        monotonic=clock.monotonic,
        wall_clock=clock.now,
    )
    monitor.sample_once()
    clock.advance(1)

    def fail_close(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(database, "close_session", fail_close)
    with pytest.raises(sqlite3.OperationalError):
        monitor.pause()
    assert not monitor.is_paused
