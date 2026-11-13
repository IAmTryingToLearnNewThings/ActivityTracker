"""Formatting and template helpers for generated reports."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Iterable

from core.database import UsageTotal

_WEEKDAY_NAMES = ("Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela")


def format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_left = divmod(remainder, 60)
    if hours:
        return f"{hours} godz. {minutes:02d} min"
    if minutes:
        return f"{minutes} min {seconds_left:02d} s"
    return f"{seconds_left} s"


def aggregate_applications(
    totals: Iterable[UsageTotal],
) -> list[tuple[str, float]]:
    applications: dict[str, float] = defaultdict(float)
    for item in totals:
        if item.duration_seconds > 0:
            applications[item.app_name] += item.duration_seconds
    return sorted(
        applications.items(), key=lambda item: (-item[1], item[0].casefold())
    )


def weekday_name(day: date) -> str:
    return _WEEKDAY_NAMES[day.weekday()]


def load_template(name: str, values: dict[str, str]) -> str:
    template_path = Path(__file__).parent / "templates" / name
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(values)


def html_text(value: str) -> str:
    return escape(value, quote=True)


def require_date(value: date) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("Expected a date, not a datetime.")
    return value
