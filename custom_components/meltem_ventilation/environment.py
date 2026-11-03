"""Derived environmental measurements."""

from __future__ import annotations

import logging
import math

_LOGGER = logging.getLogger(__name__)


def absolute_humidity(temperature: float | None, humidity: int | None) -> float | None:
    """Estimate absolute humidity in g/m³ from °C and relative humidity percent."""

    if temperature is None or humidity is None:
        return None
    if not math.isfinite(temperature) or temperature <= -243.5 or not 0 <= humidity <= 100:
        _LOGGER.warning(
            "Cannot calculate absolute humidity from temperature %s and humidity %s",
            temperature,
            humidity,
        )
        return None
    return (
        6.112
        * math.exp(17.67 * temperature / (temperature + 243.5))
        * humidity
        * 2.1674
        / (273.15 + temperature)
    )
