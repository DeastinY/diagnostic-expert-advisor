"""Helpers for durations expressed as short strings such as ``"15m"`` or ``"1h30m"``."""

from __future__ import annotations

import re
from datetime import timedelta

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw])")


def parse_duration(value: str | timedelta | int | float) -> timedelta:
    """Parse ``"5m"``, ``"1h30m"``, ``"2d"`` or a number of seconds into a timedelta.

    A :class:`~datetime.timedelta` is returned unchanged. Numbers are seconds.
    """
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    text = value.strip().lower()
    if not text:
        raise ValueError("empty duration")
    pos = 0
    total = 0.0
    for match in _TOKEN.finditer(text):
        if match.start() != pos:
            raise ValueError(f"invalid duration {value!r}")
        total += float(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        pos = match.end()
    if pos != len(text):
        raise ValueError(f"invalid duration {value!r}; use forms like '30s', '15m', '1h30m', '2d'")
    return timedelta(seconds=total)


def duration_seconds(value: str | timedelta | int | float) -> int:
    """Return a duration as a whole number of seconds (at least one)."""
    seconds = int(round(parse_duration(value).total_seconds()))
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return seconds


def format_duration(value: timedelta | float | int) -> str:
    """Render a duration compactly, e.g. ``2d 4h`` or ``15m``."""
    seconds = int(round(value.total_seconds() if isinstance(value, timedelta) else value))
    if seconds == 0:
        return "0s"
    parts: list[str] = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{unit}")
            seconds %= size
        if len(parts) == 2:
            break
    return " ".join(parts)


RESAMPLE_LADDER = ("1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d")


def snap_to_ladder(seconds: float) -> str:
    """Pick the coarsest ladder step that is at most ``seconds`` (never finer than 1m)."""
    best = RESAMPLE_LADDER[0]
    for step in RESAMPLE_LADDER:
        if duration_seconds(step) <= seconds:
            best = step
    return best
