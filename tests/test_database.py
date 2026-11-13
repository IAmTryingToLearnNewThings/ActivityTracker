from datetime import date, datetime, timedelta, timezone

import pytest

from core.database import Database


@pytest.fixture
def database():
    instance = Database(":memory:")
    try:
        yield instance
    finally:
        instance.close()


def test_usage_is_aggregated_by_app_and_process(database):
    start = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    first = database.start_session("Editor", "code.exe", start, date(2026, 11, 12))
    database.checkpoint_session(first, start + timedelta(seconds=30), 30)
    database.close_session(first, start + timedelta(seconds=60), 60)

    second = database.start_session(
        "Editor", "code.exe", start + timedelta(hours=1), date(2026, 11, 12)
    )
    database.close_session(second, start + timedelta(hours=1, seconds=15), 15)

    totals = database.get_usage_totals(date(2026, 11, 12), date(2026, 11, 12))
    activity = database.get_daily_activity(date(2026, 11, 12), date(2026, 11, 12))

    assert [(item.app_name, item.process_name, item.duration_seconds) for item in totals] == [
        ("Editor", "code.exe", 75)
    ]
    assert activity[0].day == date(2026, 11, 12)
    assert activity[0].duration_seconds == 75


def test_usage_totals_use_friendly_names_for_legacy_process_records(database):
    day = date(2026, 11, 12)
    start = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    for app_name, process_name, seconds in (
        ("code", "code.exe", 30),
        ("fortniteclient-win64-shipping", "FortniteClient-Win64-Shipping.exe", 60),
    ):
        session_id = database.start_session(app_name, process_name, start, day)
        database.close_session(
            session_id,
            start + timedelta(seconds=seconds),
            seconds,
        )

    totals = database.get_usage_totals(day, day)

    assert [(item.app_name, item.process_name) for item in totals] == [
        ("Fortnite", "FortniteClient-Win64-Shipping.exe"),
        ("Visual Studio Code", "code.exe"),
    ]


def test_recovery_closes_session_at_last_checkpoint(database):
    start = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    session_id = database.start_session("Browser", "chrome.exe", start, start.date())
    checkpoint = start + timedelta(seconds=20)
    database.checkpoint_session(session_id, checkpoint, 18)

    assert database.recover_open_sessions() == 1
    assert database.recover_open_sessions() == 0
    assert database.get_usage_totals(start.date(), start.date())[0].duration_seconds == 18

    with pytest.raises(ValueError, match="No open usage session"):
        database.close_session(session_id, checkpoint, 18)


def test_checkpoint_rejects_decreasing_duration(database):
    start = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    session_id = database.start_session("Editor", "code.exe", start, start.date())
    database.checkpoint_session(session_id, start + timedelta(seconds=10), 10)

    with pytest.raises(ValueError, match="cannot decrease"):
        database.checkpoint_session(session_id, start + timedelta(seconds=11), 9)


def test_settings_are_upserted(database):
    database.set_setting("idle_timeout", "300")
    database.set_setting("idle_timeout", "600")

    assert database.get_setting("idle_timeout") == "600"
    assert database.get_setting("missing", "fallback") == "fallback"


def test_report_attempts_keep_success_and_failure_history(database):
    attempted = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)
    sent = attempted + timedelta(seconds=2)
    database.record_report_attempt(
        "daily", date(2026, 11, 12), date(2026, 11, 12),
        "user@example.com", "sent", attempted, sent_at=sent,
    )
    database.record_report_attempt(
        "weekly", date(2026, 11, 2), date(2026, 11, 8),
        "user@example.com", "failed", attempted + timedelta(minutes=1),
        error_message="SMTP unavailable",
    )

    history = database.get_report_history()
    assert [entry.status for entry in history] == ["failed", "sent"]
    assert history[1].sent_at == sent
    assert history[0].error_message == "SMTP unavailable"


def test_file_database_persists_settings_and_usage(tmp_path):
    database_path = tmp_path / "activity-tracker.sqlite3"
    day = date(2026, 11, 12)
    start = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)

    database = Database(database_path)
    database.set_setting("idle_timeout", "300")
    session_id = database.start_session("Editor", "code.exe", start, day)
    database.close_session(session_id, start + timedelta(seconds=12), 12)
    database.close()

    reopened = Database(database_path)
    try:
        assert reopened.get_setting("idle_timeout") == "300"
        assert reopened.get_usage_totals(day, day)[0].duration_seconds == 12
    finally:
        reopened.close()


def test_hourly_activity_distributes_sessions_across_hour_boundaries(database):
    day = date(2026, 11, 12)
    start = datetime(2026, 11, 12, 10, 30).astimezone()
    session_id = database.start_session("Editor", "code.exe", start, day)
    database.close_session(session_id, start + timedelta(hours=2), 7200)

    activity = database.get_hourly_activity(day)

    assert len(activity) == 24
    assert [item.duration_seconds for item in activity[9:13]] == pytest.approx(
        [0, 1800, 3600, 1800]
    )
    assert sum(item.duration_seconds for item in activity) == pytest.approx(7200)
