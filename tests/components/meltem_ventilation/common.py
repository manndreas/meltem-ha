"""Test doubles shared by the platform tests."""

from __future__ import annotations

from unittest.mock import MagicMock, create_autospec

from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.models import RoomState
from custom_components.meltem_ventilation.polling import PollJob

TEST_ENTRY_ID = "test-entry-id"


def mock_coordinator() -> MagicMock:
    """Return a coordinator double that only offers the real coordinator's interface.

    Methods keep their real signatures, and a misspelled or removed member
    fails the test instead of returning another mock. Attributes the real
    coordinator only sets in ``__init__``, such as ``rooms`` or ``client``,
    have to be set by the test.
    """

    coordinator = create_autospec(MeltemDataUpdateCoordinator, instance=True)
    coordinator.entry_id = TEST_ENTRY_ID
    coordinator.hass = None
    coordinator.last_update_success = True
    return coordinator


# The scheduler tests drive single steps of a real coordinator. These helpers
# are the only place that reaches into its internals for that, so a change to
# them breaks one helper instead of every test.


def skip_startup(coordinator: MeltemDataUpdateCoordinator) -> None:
    """Treat the first pass over all read groups as done."""

    coordinator._startup_jobs = []


def allow_next_read(coordinator: MeltemDataUpdateCoordinator) -> None:
    """Let the next read start now, regardless of the request-rate cap."""

    coordinator._last_read_started = None


async def run_tick(coordinator: MeltemDataUpdateCoordinator) -> dict[str, RoomState]:
    """Run one poll tick and return its data; failures raise like in Home Assistant."""

    return await coordinator._async_update_data()


async def run_job(
    coordinator: MeltemDataUpdateCoordinator,
    states: dict[str, RoomState],
    job: PollJob,
) -> dict[str, RoomState]:
    """Run one read job on ``states`` and return the merged state map."""

    return await coordinator._read_one_job(states, job)
