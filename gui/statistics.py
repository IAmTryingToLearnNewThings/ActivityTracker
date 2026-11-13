"""Daily, weekly, and cross-day statistics view."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from core.database import Database, UsageTotal
from gui.formatting import format_duration

logger = logging.getLogger(__name__)

_WEEKDAY_NAMES = ("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Niedz")


class Statistics(QWidget):
    """Present daily and weekly usage and compare two selected dates."""

    def __init__(
        self,
        database: Database,
        parent: QWidget | None = None,
        *,
        refresh_interval_ms: int = 30_000,
    ) -> None:
        super().__init__(parent)
        if refresh_interval_ms < 1:
            raise ValueError("Refresh interval must be greater than zero.")
        self._database = database
        self.setObjectName("statistics")
        self.setStyleSheet(
            """
            QWidget#statistics { background: #f3f6fa; color: #172338; }
            QLabel#pageTitle { font-size: 25px; font-weight: 700; }
            QLabel#subtitle, QLabel#status { color: #718096; }
            QFrame#summaryCard, QFrame#contentCard, QFrame#filterCard {
                background: #ffffff;
                border: 1px solid #e3e9f1;
                border-radius: 12px;
            }
            QLabel.cardTitle { color: #718096; font-size: 12px; }
            QLabel.cardValue { color: #172338; font-size: 19px; font-weight: 700; }
            QLabel.sectionTitle { font-size: 16px; font-weight: 650; }
            QDateEdit {
                border: 1px solid #d5deea;
                border-radius: 6px;
                padding: 6px 8px;
                background: #ffffff;
            }
            QTableWidget {
                background: #ffffff;
                border: 0;
                gridline-color: #edf1f5;
                selection-background-color: #e8f1ff;
                selection-color: #172338;
            }
            QHeaderView::section {
                background: #f7f9fc;
                color: #64748b;
                border: 0;
                padding: 8px;
                font-weight: 600;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 22, 24, 20)
        content_layout.setSpacing(15)

        title = QLabel("Statystyki")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Raporty dzienne, tygodniowe i porównanie aplikacji")
        subtitle.setObjectName("subtitle")
        content_layout.addWidget(title)
        content_layout.addWidget(subtitle)

        filters = QFrame()
        filters.setObjectName("filterCard")
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(14, 11, 14, 11)
        filters_layout.setSpacing(12)
        self.report_date = self._add_date_filter(
            filters_layout, "Dzień raportu", QDate.currentDate()
        )
        self.compare_first_date = self._add_date_filter(
            filters_layout, "Porównaj dzień", QDate.currentDate().addDays(-1)
        )
        self.compare_second_date = self._add_date_filter(
            filters_layout, "Z dniem", QDate.currentDate()
        )
        filters_layout.addStretch(1)
        content_layout.addWidget(filters)

        summary = QHBoxLayout()
        summary.setSpacing(11)
        self.daily_total_value = self._add_summary_card(
            summary, "Aktywność wybranego dnia"
        )
        self.weekly_total_value = self._add_summary_card(
            summary, "Aktywność w tygodniu"
        )
        self.daily_average_value = self._add_summary_card(
            summary, "Średnia dzienna"
        )
        self.peak_day_value = self._add_summary_card(
            summary, "Najaktywniejszy dzień"
        )
        content_layout.addLayout(summary)

        chart_card = QFrame()
        chart_card.setObjectName("contentCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(14, 11, 14, 10)
        self.figure = Figure(figsize=(10, 3.4), dpi=100, constrained_layout=True)
        self.figure.set_facecolor("#ffffff")
        self.weekly_axes, self.comparison_axes = self.figure.subplots(1, 2)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.canvas.setMinimumHeight(285)
        chart_layout.addWidget(self.canvas)
        content_layout.addWidget(chart_card)

        tables = QHBoxLayout()
        tables.setSpacing(12)
        self.daily_table = self._create_table(
            ["Aplikacja", "Proces", "Czas", "Udział"]
        )
        self.comparison_table = self._create_table(
            ["Aplikacja", "Pierwszy dzień", "Drugi dzień", "Różnica"]
        )
        tables.addWidget(
            self._table_card("Aplikacje wybranego dnia", self.daily_table)
        )
        tables.addWidget(
            self._table_card("Porównanie aplikacji", self.comparison_table)
        )
        content_layout.addLayout(tables)

        self.scroll_area.setWidget(content)
        root.addWidget(self.scroll_area)
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)

        self.report_date.dateChanged.connect(self.refresh)
        self.compare_first_date.dateChanged.connect(self.refresh)
        self.compare_second_date.dateChanged.connect(self.refresh)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(refresh_interval_ms)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    @staticmethod
    def _add_date_filter(
        layout: QHBoxLayout, label_text: str, initial_date: QDate
    ) -> QDateEdit:
        label = QLabel(label_text)
        date_edit = QDateEdit(initial_date)
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("yyyy-MM-dd")
        date_edit.setMaximumDate(QDate.currentDate())
        layout.addWidget(label)
        layout.addWidget(date_edit)
        return date_edit

    @staticmethod
    def _add_summary_card(layout: QHBoxLayout, title: str) -> QLabel:
        card = QFrame()
        card.setObjectName("summaryCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 11, 13, 12)
        card_layout.setSpacing(7)
        title_label = QLabel(title)
        title_label.setProperty("class", "cardTitle")
        value_label = QLabel("—")
        value_label.setProperty("class", "cardValue")
        value_label.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        layout.addWidget(card, stretch=1)
        return value_label

    @staticmethod
    def _create_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        table.setMinimumHeight(215)
        return table

    @staticmethod
    def _table_card(title: str, table: QTableWidget) -> QFrame:
        card = QFrame()
        card.setObjectName("contentCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 13)
        heading = QLabel(title)
        heading.setProperty("class", "sectionTitle")
        layout.addWidget(heading)
        layout.addWidget(table)
        return card

    def refresh(self) -> None:
        selected_day = self.report_date.date().toPython()
        first_day = self.compare_first_date.date().toPython()
        second_day = self.compare_second_date.date().toPython()
        week_start = selected_day - timedelta(days=selected_day.weekday())
        week_end = week_start + timedelta(days=6)

        try:
            daily_totals = self._database.get_usage_totals(
                selected_day, selected_day
            )
            weekly_activity = self._database.get_daily_activity(
                week_start, week_end
            )
            first_totals = self._database.get_usage_totals(first_day, first_day)
            second_totals = self._database.get_usage_totals(second_day, second_day)
        except sqlite3.Error:
            logger.exception("Could not load statistics.")
            self.status_label.setText("Nie udało się odczytać statystyk.")
            return

        daily_seconds = sum(item.duration_seconds for item in daily_totals)
        daily_map = {
            week_start + timedelta(days=offset): 0.0 for offset in range(7)
        }
        daily_map.update(
            {item.day: item.duration_seconds for item in weekly_activity}
        )
        weekly_seconds = sum(daily_map.values())
        peak_day = max(daily_map, key=daily_map.get)
        peak_seconds = daily_map[peak_day]

        self.daily_total_value.setText(format_duration(daily_seconds))
        self.weekly_total_value.setText(format_duration(weekly_seconds))
        self.daily_average_value.setText(format_duration(weekly_seconds / 7))
        self.peak_day_value.setText(
            "Brak danych"
            if peak_seconds == 0
            else f"{peak_day:%d.%m.%Y} • {format_duration(peak_seconds)}"
        )

        first_by_app = self._aggregate_by_app(first_totals)
        second_by_app = self._aggregate_by_app(second_totals)
        self._populate_daily_table(daily_totals, daily_seconds)
        self._populate_comparison_table(first_by_app, second_by_app)
        self._draw_charts(
            week_start,
            daily_map,
            first_day,
            second_day,
            first_by_app,
            second_by_app,
        )
        self.status_label.setText(
            f"Tydzień {week_start:%d.%m.%Y} – {week_end:%d.%m.%Y}"
        )

    @staticmethod
    def _aggregate_by_app(totals: list[UsageTotal]) -> dict[str, float]:
        apps: dict[str, float] = {}
        for item in totals:
            apps[item.app_name] = apps.get(item.app_name, 0.0) + item.duration_seconds
        return apps

    def _populate_daily_table(
        self, totals: list[UsageTotal], daily_seconds: float
    ) -> None:
        self.daily_table.setRowCount(len(totals))
        for row, item in enumerate(totals):
            share = item.duration_seconds / daily_seconds * 100 if daily_seconds else 0
            values = (
                item.app_name,
                item.process_name,
                format_duration(item.duration_seconds),
                f"{share:.1f}%",
            )
            self._set_table_row(self.daily_table, row, values, right_aligned=(2, 3))

    def _populate_comparison_table(
        self,
        first_by_app: dict[str, float],
        second_by_app: dict[str, float],
    ) -> None:
        app_names = sorted(
            first_by_app.keys() | second_by_app.keys(),
            key=lambda app: max(first_by_app.get(app, 0), second_by_app.get(app, 0)),
            reverse=True,
        )
        self.comparison_table.setRowCount(len(app_names))
        for row, app_name in enumerate(app_names):
            first = first_by_app.get(app_name, 0.0)
            second = second_by_app.get(app_name, 0.0)
            difference = second - first
            rounded_difference = round(difference)
            sign = (
                "+"
                if rounded_difference > 0
                else "−"
                if rounded_difference < 0
                else ""
            )
            values = (
                app_name,
                format_duration(first),
                format_duration(second),
                f"{sign}{format_duration(abs(rounded_difference))}",
            )
            self._set_table_row(
                self.comparison_table, row, values, right_aligned=(1, 2, 3)
            )

    @staticmethod
    def _set_table_row(
        table: QTableWidget,
        row: int,
        values: tuple[str, ...],
        *,
        right_aligned: tuple[int, ...] = (),
    ) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in right_aligned:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            table.setItem(row, column, item)
        table.setRowHeight(row, 32)

    def _draw_charts(
        self,
        week_start: date,
        daily_map: dict[date, float],
        first_day: date,
        second_day: date,
        first_by_app: dict[str, float],
        second_by_app: dict[str, float],
    ) -> None:
        self.weekly_axes.clear()
        self.comparison_axes.clear()

        self.weekly_axes.set_title("Aktywność tygodniowa", loc="left", fontsize=12)
        week_days = [week_start + timedelta(days=offset) for offset in range(7)]
        labels = [
            f"{_WEEKDAY_NAMES[day.weekday()]}\n{day:%d.%m}" for day in week_days
        ]
        self.weekly_axes.bar(
            range(7),
            [daily_map[day] / 60 for day in week_days],
            color="#3867d6",
            width=0.64,
        )
        self.weekly_axes.set_xticks(range(7), labels)
        self.weekly_axes.set_ylabel("Minuty", color="#718096")
        self._style_axes(self.weekly_axes, grid_axis="y")

        self.comparison_axes.set_title(
            "Czas według aplikacji", loc="left", fontsize=12
        )
        app_names = sorted(
            first_by_app.keys() | second_by_app.keys(),
            key=lambda app: max(first_by_app.get(app, 0), second_by_app.get(app, 0)),
            reverse=True,
        )[:8]
        if app_names:
            first_label = f"{first_day:%d.%m}"
            second_label = f"{second_day:%d.%m}"
            positions = list(range(len(app_names)))
            offset = 0.18
            self.comparison_axes.barh(
                [position - offset for position in positions],
                [first_by_app.get(app, 0) / 60 for app in app_names],
                height=0.34,
                color="#94a3b8",
                label=first_label,
            )
            self.comparison_axes.barh(
                [position + offset for position in positions],
                [second_by_app.get(app, 0) / 60 for app in app_names],
                height=0.34,
                color="#20a779",
                label=second_label,
            )
            self.comparison_axes.set_yticks(positions, app_names)
            self.comparison_axes.invert_yaxis()
            self.comparison_axes.legend(frameon=False, fontsize=8)
            self.comparison_axes.set_xlabel("Minuty", color="#718096")
        else:
            self.comparison_axes.text(
                0.5,
                0.5,
                "Brak danych",
                ha="center",
                va="center",
                color="#718096",
                transform=self.comparison_axes.transAxes,
            )
        self._style_axes(self.comparison_axes, grid_axis="x")
        self.canvas.draw_idle()

    @staticmethod
    def _style_axes(axes: Axes, *, grid_axis: str) -> None:
        axes.grid(axis=grid_axis, color="#e8edf4")
        axes.set_axisbelow(True)
        axes.spines[["top", "right"]].set_visible(False)
        axes.tick_params(axis="both", colors="#718096", labelsize=8)
        axes.set_facecolor("#ffffff")
