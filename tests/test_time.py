from datetime import timedelta

import pytest

from dea.core.time import duration_seconds, format_duration, parse_duration, snap_to_ladder


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30s", 30), ("15m", 900), ("1h", 3600), ("1h30m", 5400), ("2d", 172800), ("1.5h", 5400)],
)
def test_parse_duration(text: str, seconds: int) -> None:
    assert parse_duration(text) == timedelta(seconds=seconds)
    assert duration_seconds(text) == seconds


def test_parse_duration_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_duration("soon")
    with pytest.raises(ValueError):
        parse_duration("")
    with pytest.raises(ValueError):
        duration_seconds("0s")


def test_format_duration() -> None:
    assert format_duration(timedelta(hours=52)) == "2d 4h"
    assert format_duration(900) == "15m"
    assert format_duration(0) == "0s"


def test_snap_to_ladder() -> None:
    assert snap_to_ladder(60) == "1m"
    assert snap_to_ladder(899) == "5m"
    assert snap_to_ladder(3600) == "1h"
    assert snap_to_ladder(10**9) == "1d"
