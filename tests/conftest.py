"""Shared fixtures for Meltem Modbus tests."""

from __future__ import annotations

import socket
import sys

import pytest

if sys.platform == "win32":
    # The Windows proactor event loop builds an internal AF_INET socketpair when
    # the loop is created, which pytest-homeassistant-custom-component blocks.
    # On Linux asyncio uses a pipe there, so CI is unaffected.
    # Only that loopback pair is let through; the socket guard itself stays
    # active, so tests still cannot reach the network.
    _real_socket_cls = socket.socket
    _real_socketpair = socket.socketpair

    def _loopback_socketpair(*args, **kwargs):
        guarded_socket_cls = socket.socket
        socket.socket = _real_socket_cls
        try:
            return _real_socketpair(*args, **kwargs)
        finally:
            socket.socket = guarded_socket_cls

    socket.socketpair = _loopback_socketpair


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow pytest-homeassistant-custom-component to load our integration."""
    yield
