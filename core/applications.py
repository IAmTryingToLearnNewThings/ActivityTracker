"""Human-readable names for known application processes."""

from __future__ import annotations

import ntpath

_DISPLAY_NAMES = {
    "code": "Visual Studio Code",
}


def display_name_for_process(process_name: str) -> str:
    executable = ntpath.basename(process_name.strip())
    stem = ntpath.splitext(executable)[0]
    normalized = stem.casefold()

    if normalized in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[normalized]
    if normalized.startswith("fortniteclient-"):
        return "Fortnite"
    return stem
