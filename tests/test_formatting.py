from __future__ import annotations

import pytest

from compress_mov.formatting import clock, delta_phrase, human


@pytest.mark.parametrize(
    ("nbytes", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1.0 MB"),
        (5 * 1024**2, "5.0 MB"),
        (1024**3, "1.00 GB"),
        (int(2.5 * 1024**3), "2.50 GB"),
    ],
)
def test_human(nbytes: int, expected: str) -> None:
    assert human(nbytes) == expected


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (0.0, "0.0% smaller"),
        (25.5, "25.5% smaller"),
        (100.0, "100.0% smaller"),
        (-10.2, "10.2% larger"),
    ],
)
def test_delta_phrase(pct: float, expected: str) -> None:
    assert delta_phrase(pct) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5, "0:00"),
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
    ],
)
def test_clock(seconds: float, expected: str) -> None:
    assert clock(seconds) == expected


@pytest.mark.parametrize(
    ("in_bytes", "out_bytes", "expected"),
    [(1000, 250, 75.0), (1000, 1000, 0.0), (1000, 1500, -50.0), (0, 0, 0.0), (0, 10, 0.0)],
)
def test_saved_pct(in_bytes: int, out_bytes: int, expected: float) -> None:
    from compress_mov.formatting import saved_pct

    assert saved_pct(in_bytes, out_bytes) == expected
