"""Generation of weekly HTML reports and their chart image."""

from __future__ import annotations

from datetime import date, timedelta
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
    weekday_name,
)
from reports.models import GeneratedReport


class WeeklyReport:
    def __init__(self, database: Database) -> None:
        self._database = database

    def generate(self, week_of: date) -> GeneratedReport:
        week_of = require_date(week_of)
        week_start = week_of - timedelta(days=week_of.weekday())
        week_end = week_start + timedelta(days=6)
        applications = aggregate_applications(
            self._database.get_usage_totals(week_start, week_end)
        )
        daily_activity = self._database.get_daily_activity(week_start, week_end)
        daily_seconds = {
            week_start + timedelta(days=offset): 0.0 for offset in range(7)
        }
        daily_seconds.update(
            {activity.day: activity.duration_seconds for activity in daily_activity}
        )

        total_seconds = sum(duration for _, duration in applications)
        peak_day = max(daily_seconds, key=daily_seconds.get)
        peak_seconds = daily_seconds[peak_day]
        day_rows = "".join(
            "<tr>"
            f"<td>{weekday_name(day)}</td>"
            f"<td>{day:%d.%m.%Y}</td>"
            f"<td>{format_duration(daily_seconds[day])}</td>"
            "</tr>"
            for day in daily_seconds
        )
        if applications:
            app_rows = "".join(
                "<tr>"
                f"<td>{html_text(app_name)}</td>"
                f"<td>{format_duration(duration)}</td>"
                f"<td>{duration / total_seconds * 100:.1f}%</td>"
                "</tr>"
                for app_name, duration in applications
            )
        else:
            app_rows = '<tr><td colspan="3">Brak zarejestrowanej aktywności</td></tr>'

        peak_label = (
            "Brak danych"
            if peak_seconds == 0
            else f"{weekday_name(peak_day)}, {peak_day:%d.%m.%Y} "
            f"({format_duration(peak_seconds)})"
        )
        chart_cid = f"weekly-usage-{week_start:%Y%m%d}"
        html = load_template(
            "weekly.html",
            {
                "week_range": (
                    f"{week_start:%d.%m.%Y} – {week_end:%d.%m.%Y}"
                ),
                "total_time": format_duration(total_seconds),
                "daily_average": format_duration(total_seconds / 7),
                "peak_day": html_text(peak_label),
                "day_rows": day_rows,
                "app_rows": app_rows,
                "chart_cid": chart_cid,
            },
        )
        return GeneratedReport(
            subject=(
                f"ActivityTracker — raport tygodniowy "
                f"{week_start:%d.%m}–{week_end:%d.%m.%Y}"
            ),
            html=html,
            chart_png=self._create_chart(daily_seconds, applications),
            chart_cid=chart_cid,
        )

    @staticmethod
    def _create_chart(
        daily_seconds: dict[date, float],
        applications: list[tuple[str, float]],
    ) -> bytes:
        figure = Figure(figsize=(10, 4.5), dpi=140, facecolor="#ffffff")
        daily_axes, app_axes = figure.subplots(1, 2)

        days = list(daily_seconds)
        daily_axes.bar(
            range(len(days)),
            [daily_seconds[day] / 60 for day in days],
            color="#3867d6",
            width=0.64,
        )
        daily_axes.set_xticks(
            range(len(days)),
            [f"{weekday_name(day)[:3]}\n{day:%d.%m}" for day in days],
        )
        daily_axes.set_ylabel("Minuty", color="#718096")
        daily_axes.set_title("Aktywność według dni", loc="left", fontsize=11)
        daily_axes.grid(axis="y", color="#e8edf4")
        daily_axes.set_axisbelow(True)
        daily_axes.spines[["top", "right"]].set_visible(False)
        daily_axes.tick_params(axis="both", colors="#718096", labelsize=8)

        top_apps = applications[:8]
        if top_apps:
            top_apps = list(reversed(top_apps))
            app_axes.barh(
                range(len(top_apps)),
                [duration / 60 for _, duration in top_apps],
                color="#20a779",
                height=0.65,
            )
            app_axes.set_yticks(range(len(top_apps)), [app for app, _ in top_apps])
            app_axes.set_xlabel("Minuty", color="#718096")
        else:
            app_axes.text(
                0.5,
                0.5,
                "Brak danych",
                ha="center",
                va="center",
                color="#718096",
                transform=app_axes.transAxes,
            )
        app_axes.set_title("Aplikacje", loc="left", fontsize=11)
        app_axes.grid(axis="x", color="#e8edf4")
        app_axes.set_axisbelow(True)
        app_axes.spines[["top", "right"]].set_visible(False)
        app_axes.tick_params(axis="both", colors="#718096", labelsize=8)

        figure.tight_layout()
        output = BytesIO()
        FigureCanvasAgg(figure).print_png(output)
        return output.getvalue()
