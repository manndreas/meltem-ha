"""Tests for locally derived environmental measurements."""

import math

import pytest

from custom_components.meltem_ventilation.environment import absolute_humidity


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [(20.0, 50, 8.639), (20.0, 0, 0.0), (0.0, 100, 4.85)],
)
def test_absolute_humidity_known_values(temperature: float, humidity: int, expected: float) -> None:
    assert absolute_humidity(temperature, humidity) == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize(
    ("temperature", "humidity"),
    [(None, 50), (20.0, None), (math.nan, 50), (-243.5, 50), (20.0, -1), (20.0, 101)],
)
def test_absolute_humidity_rejects_missing_or_invalid_inputs(
    temperature: float | None, humidity: int | None
) -> None:
    assert absolute_humidity(temperature, humidity) is None
