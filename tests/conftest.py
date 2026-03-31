"""Shared fixtures for Meltem Modbus tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow pytest-homeassistant-custom-component to load our integration."""
    yield
