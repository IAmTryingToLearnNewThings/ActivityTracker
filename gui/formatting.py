"""Formatting helpers shared by GUI views."""


def format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_left = divmod(remainder, 60)
    if hours:
        return f"{hours} godz. {minutes:02d} min"
    if minutes:
        return f"{minutes} min {seconds_left:02d} s"
    return f"{seconds_left} s"
