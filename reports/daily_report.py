"""Generation of daily HTML reports and their chart image."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from core.database import Database
from reports.formatting import (
    aggregate_applications,
    format_duration,
    html_text,
    load_template,
    require_date,
)
from reports.models import GeneratedReport

_CHART_COLORS = ("#3867d6", "#20a779", "#f2a541", "#8d6be8", "#e36b82", "#94a3b8")


class DailyReport:
    def __init__(self, database: Database) -> None:
        self._database = database

    def generate(self, report_date: date) -> GeneratedReport:
        report_date = require_date(report_date)
        applications = aggregate_applications(
            self._database.get_usage_totals(report_date, report_date)
        )
        total_seconds = sum(duration for _, duration in applications)

        if applications:
            rows = "".join(
                "<tr>"
                f"<td>{html_text(app_name)}</td>"
                f"<td>{format_duration(duration)}</td>"
                f"<td>{duration / total_seconds * 100:.1f}%</td>"
                "</tr>"
                for app_name, duration in applications
            )
            top_apps = "".join(
                f"<li><strong>{html_text(app_name)}</strong> — "
                f"{format_duration(duration)}</li>"
                for app_name, duration in applications[:3]
            )
        else:
            rows = '<tr><td colspan="3">Brak zarejestrowanej aktywności</td></tr>'
            top_apps = "<li>Brak danych</li>"

        chart_cid = f"daily-usage-{report_date:%Y%m%d}"
        html = load_template(
            "daily.html",
            {
                "report_date": html_text(report_date.strftime("%d.%m.%Y")),
                "total_time": format_duration(total_seconds),
                "app_rows": rows,
                "top_apps": top_apps,
                "chart_cid": chart_cid,
            },
        )
        return GeneratedReport(
            subject=f"ActivityTracker — raport dzienny {report_date:%d.%m.%Y}",
            html=html,
            chart_png=self._create_chart(applications),
            chart_cid=chart_cid,
        )

    @staticmethod
    def _create_chart(applications: list[tuple[str, float]]) -> bytes:
        figure = Figure(figsize=(7, 4), dpi=140, facecolor="#ffffff")
        axes = figure.subplots()
        if applications:
            labels = [app for app, _ in applications[:5]]
            values = [duration for _, duration in applications[:5]]
            other = sum(duration for _, duration in applications[5:])
            if other:
                labels.append("Pozostałe")
                values.append(other)
            axes.pie(
                values,
                labels=labels,
                autopct=lambda percent: f"{percent:.0f}%",
                startangle=90,
                colors=_CHART_COLORS,
                textprops={"fontsize": 8, "color": "#475569"},
            )
            axes.axis("equal")
        else:
            axes.text(
                0.5,
                0.5,
                "Brak danych",
                ha="center",
                va="center",
                color="#718096",
                transform=axes.transAxes,
            )
            axes.set_axis_off()

        output = BytesIO()
        FigureCanvasAgg(figure).print_png(output)
        return output.getvalue()
