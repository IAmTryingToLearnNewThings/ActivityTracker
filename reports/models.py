"""Shared report content types."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedReport:
    subject: str
    html: str
    chart_png: bytes
    chart_cid: str
