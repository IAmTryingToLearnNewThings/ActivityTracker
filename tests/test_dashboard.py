import os
from datetime import date, datetime, time, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.database import Database
from gui.dashboard import Dashboard


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


def test_dashboard_shows_today_summary_and_charts(database, qt_application):
    today = date.today()
    start = datetime.combine(today, time(hour=9)).astimezone()
    session_id = database.start_session("Editor", "code.exe", start, today)
    database.close_session(session_id, start + timedelta(minutes=90), 5400)
    dashboard = Dashboard(database, refresh_interval_ms=60_000)
    try:
        assert dashboard.total_time_value.text() == "1 godz. 30 min"
        assert dashboard.top_app_value.text() == "Editor"
        assert dashboard.top_app_time_value.text() == "1 godz. 30 min"
        assert dashboard.apps_table.rowCount() == 1
        assert dashboard.apps_table.item(0, 3).text() == "100.0%"
        assert len(dashboard.hour_axes.patches) == 24
        assert dashboard.pie_axes.patches
    finally:
        dashboard._refresh_timer.stop()
        dashboard.close()


def test_empty_dashboard_displays_zero_activity(database, qt_application):
    dashboard = Dashboard(database, refresh_interval_ms=60_000)
    try:
        assert dashboard.total_time_value.text() == "0 s"
        assert dashboard.top_app_value.text() == "Brak danych"
        assert dashboard.apps_table.rowCount() == 0
    finally:
        dashboard._refresh_timer.stop()
        dashboard.close()
