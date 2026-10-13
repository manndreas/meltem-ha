"""Shared fixtures for Meltem Modbus tests."""

from __future__ import annotations

import socket
import sys

import pytest

if sys.platform == "win32":
    # The proactor event loop creates an AF_INET socketpair, which the harness's
    # socket guard blocks. Only that loopback pair is let through; Linux uses a
    # pipe there, so CI runs with the plain guard.
    _real_socket = socket.socket
    _real_socketpair = socket.socketpair

    def _loopback_socketpair(*args, **kwargs):
        guarded_socket = socket.socket
        socket.socket = _real_socket
        try:
            return _real_socketpair(*args, **kwargs)
        finally:
            socket.socket = guarded_socket

    socket.socketpair = _loopback_socketpair


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow pytest-homeassistant-custom-component to load the integration."""
