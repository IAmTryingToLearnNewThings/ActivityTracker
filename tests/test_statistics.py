import os
from datetime import date, datetime, time, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from core.database import Database
from gui.statistics import Statistics


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


def _record_usage(database, day: date, app_name: str, process_name: str, seconds: int):
    start = datetime.combine(day, time(hour=9)).astimezone()
    session_id = database.start_session(app_name, process_name, start, day)
    database.close_session(
        session_id, start + timedelta(seconds=seconds), seconds
    )


def test_statistics_show_daily_weekly_and_comparison_data(database, qt_application):
    first_day = date(2026, 11, 12)
    second_day = date(2026, 11, 13)
    _record_usage(database, first_day, "Code", "code.exe", 60)
    _record_usage(database, second_day, "Code", "code.exe", 180)
    _record_usage(database, second_day, "Firefox", "firefox.exe", 30)

    statistics = Statistics(database, refresh_interval_ms=60_000)
    try:
        statistics.report_date.setDate(QDate(2026, 11, 13))
        statistics.compare_first_date.setDate(QDate(2026, 11, 12))
        statistics.compare_second_date.setDate(QDate(2026, 11, 13))
        statistics.refresh()

        assert statistics.daily_total_value.text() == "3 min 30 s"
        assert statistics.weekly_total_value.text() == "4 min 30 s"
        assert statistics.daily_average_value.text() == "39 s"
        assert statistics.peak_day_value.text() == "13.11.2026 • 3 min 30 s"
        assert statistics.daily_table.rowCount() == 2
        comparison = {
            statistics.comparison_table.item(row, 0).text():
            statistics.comparison_table.item(row, 3).text()
            for row in range(statistics.comparison_table.rowCount())
        }
        assert comparison == {"Code": "+2 min 00 s", "Firefox": "+30 s"}
        assert len(statistics.weekly_axes.patches) == 7
        assert len(statistics.comparison_axes.patches) == 4
    finally:
        statistics._refresh_timer.stop()
        statistics.close()


def test_statistics_handle_empty_periods(database, qt_application):
    statistics = Statistics(database, refresh_interval_ms=60_000)
    try:
        assert statistics.daily_total_value.text() == "0 s"
        assert statistics.weekly_total_value.text() == "0 s"
        assert statistics.peak_day_value.text() == "Brak danych"
        assert statistics.daily_table.rowCount() == 0
        assert statistics.comparison_table.rowCount() == 0
    finally:
        statistics._refresh_timer.stop()
        statistics.close()
