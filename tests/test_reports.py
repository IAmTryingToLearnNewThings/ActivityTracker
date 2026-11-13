from datetime import date, datetime, time, timedelta

import pytest

from core.database import Database
from reports.daily_report import DailyReport
from reports.weekly_report import WeeklyReport


@pytest.fixture
def database():
    instance = Database(":memory:")
    try:
        yield instance
    finally:
        instance.close()


def _record_usage(database, day, app_name, process_name, seconds):
    start = datetime.combine(day, time(hour=9)).astimezone()
    session_id = database.start_session(app_name, process_name, start, day)
    database.close_session(
        session_id, start + timedelta(seconds=seconds), seconds
    )


def test_daily_report_includes_totals_top_apps_and_safe_html(database):
    report_date = date(2026, 11, 12)
    _record_usage(database, report_date, "<Editor & Tools>", "code.exe", 5400)
    _record_usage(database, report_date, "Firefox", "firefox.exe", 1800)

    report = DailyReport(database).generate(report_date)

    assert "12.11.2026" in report.subject
    assert "2 godz. 00 min" in report.html
    assert "&lt;Editor &amp; Tools&gt;" in report.html
    assert "<Editor & Tools>" not in report.html
    assert "75.0%" in report.html
    assert "Firefox" in report.html
    assert f'cid:{report.chart_cid}' in report.html
    assert report.chart_png.startswith(b"\x89PNG\r\n\x1a\n")


def test_empty_daily_report_renders_without_division_by_zero(database):
    report = DailyReport(database).generate(date(2026, 11, 12))

    assert "0 s" in report.html
    assert "Brak zarejestrowanej aktywności" in report.html
    assert report.chart_png.startswith(b"\x89PNG\r\n\x1a\n")


def test_daily_report_limits_top_apps_to_three(database):
    report_date = date(2026, 11, 12)
    _record_usage(database, report_date, "Fourth", "fourth.exe", 100)
    _record_usage(database, report_date, "Third", "third.exe", 200)
    _record_usage(database, report_date, "Second", "second.exe", 300)
    _record_usage(database, report_date, "First", "first.exe", 400)

    report = DailyReport(database).generate(report_date)
    top_apps = report.html.split("<ol>", 1)[1].split("</ol>", 1)[0]

    assert top_apps.count("<li>") == 3
    assert top_apps.index("First") < top_apps.index("Second") < top_apps.index("Third")
    assert "Fourth" not in top_apps


def test_weekly_report_summarizes_week_and_peak_day(database):
    _record_usage(database, date(2026, 11, 9), "Code", "code.exe", 3600)
    _record_usage(database, date(2026, 11, 11), "Firefox", "firefox.exe", 7200)
    _record_usage(database, date(2026, 11, 13), "Code", "code.exe", 1800)

    report = WeeklyReport(database).generate(date(2026, 11, 11))

    assert "09.11.2026 – 15.11.2026" in report.html
    assert "3 godz. 30 min" in report.html
    assert "30 min 00 s" in report.html
    assert "Środa, 11.11.2026 (2 godz. 00 min)" in report.html
    assert "Code" in report.html
    assert "Firefox" in report.html
    assert report.chart_png.startswith(b"\x89PNG\r\n\x1a\n")


def test_weekly_report_with_no_usage_still_has_complete_week(database):
    report = WeeklyReport(database).generate(date(2026, 11, 11))

    assert report.html.count("<tr>") == 10
    assert "Brak danych" in report.html
    assert report.chart_png.startswith(b"\x89PNG\r\n\x1a\n")
