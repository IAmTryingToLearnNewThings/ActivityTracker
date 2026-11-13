"""Dashboard view for today's application usage."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from core.database import Database, HourlyActivity, UsageTotal
from gui.formatting import format_duration

logger = logging.getLogger(__name__)


class Dashboard(QWidget):
    """Show today's total, leading applications, and usage charts."""

    def __init__(
        self,
        database: Database,
        parent: QWidget | None = None,
        *,
        refresh_interval_ms: int = 15_000,
    ) -> None:
        super().__init__(parent)
        if refresh_interval_ms < 1:
            raise ValueError("Refresh interval must be greater than zero.")
        self._database = database
        self.setObjectName("dashboard")
        self.setStyleSheet(
            """
            QWidget#dashboard { background: #f3f6fa; color: #172338; }
            QLabel#pageTitle { font-size: 25px; font-weight: 700; }
            QLabel#subtitle, QLabel#status { color: #718096; }
            QFrame#summaryCard, QFrame#contentCard {
                background: #ffffff;
                border: 1px solid #e3e9f1;
                border-radius: 12px;
            }
            QLabel.cardTitle { color: #718096; font-size: 12px; }
            QLabel.cardValue { color: #172338; font-size: 22px; font-weight: 700; }
            QLabel.sectionTitle { font-size: 16px; font-weight: 650; }
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
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)

        title = QLabel("Pulpit")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Podsumowanie użycia aplikacji dzisiaj")
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.total_time_value = self._add_summary_card(cards, "Aktywność dzisiaj")
        self.top_app_value = self._add_summary_card(cards, "Najczęściej używana")
        self.top_app_time_value = self._add_summary_card(
            cards, "Czas w tej aplikacji"
        )
        root.addLayout(cards)

        chart_card = QFrame()
        chart_card.setObjectName("contentCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(14, 12, 14, 10)
        self.figure = Figure(figsize=(10, 3.4), dpi=100, constrained_layout=True)
        self.figure.set_facecolor("#ffffff")
        self.pie_axes, self.hour_axes = self.figure.subplots(1, 2)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.canvas.setMinimumHeight(285)
        chart_layout.addWidget(self.canvas)
        root.addWidget(chart_card)

        table_card = QFrame()
        table_card.setObjectName("contentCard")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 13, 16, 14)
        section_title = QLabel("Czas według aplikacji")
        section_title.setProperty("class", "sectionTitle")
        table_layout.addWidget(section_title)

        self.apps_table = QTableWidget(0, 4)
        self.apps_table.setHorizontalHeaderLabels(
            ["Aplikacja", "Proces", "Czas", "Udział"]
        )
        self.apps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.apps_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.apps_table.setAlternatingRowColors(True)
        self.apps_table.verticalHeader().setVisible(False)
        self.apps_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.apps_table.setMinimumHeight(170)
        table_layout.addWidget(self.apps_table)
        root.addWidget(table_card, stretch=1)

        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(refresh_interval_ms)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    @staticmethod
    def _add_summary_card(layout: QHBoxLayout, title: str) -> QLabel:
        card = QFrame()
        card.setObjectName("summaryCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 13, 16, 14)
        card_layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setProperty("class", "cardTitle")
        value_label = QLabel("—")
        value_label.setProperty("class", "cardValue")
        value_label.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        layout.addWidget(card, stretch=1)
        return value_label

    def refresh(self) -> None:
        today = date.today()
        try:
            totals = self._database.get_usage_totals(today, today)
            hourly_activity = self._database.get_hourly_activity(today)
        except sqlite3.Error:
            logger.exception("Could not load dashboard activity.")
            self.status_label.setText("Nie udało się odczytać danych aktywności.")
            return

        total_seconds = sum(item.duration_seconds for item in totals)
        self.total_time_value.setText(format_duration(total_seconds))
        if totals:
            self.top_app_value.setText(totals[0].app_name)
            self.top_app_time_value.setText(
                format_duration(totals[0].duration_seconds)
            )
        else:
            self.top_app_value.setText("Brak danych")
            self.top_app_time_value.setText("0 s")

        self._populate_apps_table(totals, total_seconds)
        self._draw_charts(totals, hourly_activity)
        self.status_label.setText(
            f"Ostatnia aktualizacja: {datetime.now().astimezone():%H:%M:%S}"
        )

    def _populate_apps_table(
        self, totals: list[UsageTotal], total_seconds: float
    ) -> None:
        self.apps_table.setRowCount(len(totals))
        for row, item in enumerate(totals):
            share = (
                item.duration_seconds / total_seconds * 100
                if total_seconds
                else 0
            )
            values = (
                item.app_name,
                item.process_name,
                format_duration(item.duration_seconds),
                f"{share:.1f}%",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column >= 2:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.apps_table.setItem(row, column, cell)
            self.apps_table.setRowHeight(row, 34)

    def _draw_charts(
        self,
        totals: list[UsageTotal],
        hourly_activity: list[HourlyActivity],
    ) -> None:
        self.pie_axes.clear()
        self.hour_axes.clear()

        self.pie_axes.set_title("Podział czasu", loc="left", fontsize=12, pad=12)
        if totals:
            labels = [item.app_name for item in totals[:5]]
            values = [item.duration_seconds for item in totals[:5]]
            remainder = sum(item.duration_seconds for item in totals[5:])
            if remainder:
                labels.append("Pozostałe")
                values.append(remainder)
            self.pie_axes.pie(
                values,
                labels=labels,
                autopct=lambda percent: f"{percent:.0f}%" if percent >= 5 else "",
                startangle=90,
                textprops={"fontsize": 8, "color": "#475569"},
                colors=["#3867d6", "#20a779", "#f2a541", "#8d6be8", "#e36b82", "#94a3b8"],
            )
            self.pie_axes.axis("equal")
        else:
            self.pie_axes.text(
                0.5,
                0.5,
                "Brak danych",
                ha="center",
                va="center",
                color="#718096",
                transform=self.pie_axes.transAxes,
            )
            self.pie_axes.set_axis_off()

        self.hour_axes.set_title(
            "Aktywność w ciągu dnia", loc="left", fontsize=12, pad=12
        )
        hourly = {item.hour: item.duration_seconds for item in hourly_activity}
        hours = range(24)
        self.hour_axes.bar(
            hours,
            [hourly.get(hour, 0) / 60 for hour in hours],
            color="#3867d6",
            width=0.72,
        )
        self.hour_axes.set_xticks(
            range(0, 24, 3), [f"{hour:02d}:00" for hour in range(0, 24, 3)]
        )
        self.hour_axes.set_ylabel("Minuty", color="#718096")
        self.hour_axes.grid(axis="y", color="#e8edf4", linewidth=0.8)
        self.hour_axes.set_axisbelow(True)
        self.hour_axes.spines[["top", "right", "left"]].set_visible(False)
        self.hour_axes.tick_params(axis="both", colors="#718096", labelsize=8)
        self.hour_axes.set_facecolor("#ffffff")
        self.canvas.draw_idle()
