"""Tests for the register model of the mock gateway."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.meltem_ventilation.const import (
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_FILTER_CHANGE_DUE,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_GATEWAY_UPTIME,
    REGISTER_GATEWAY_UTC_CLOCK,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    REGISTER_PRESET_MODE,
    REGISTER_PRESET_VALUE,
    profile_max_airflow,
)
from tools._mock_model import (
    ACKNOWLEDGE,
    ILLEGAL_DATA_ADDRESS,
    ILLEGAL_DATA_VALUE,
    INITIAL_UPTIME_SECONDS,
    MockGatewayModel,
    RegisterMapError,
    load_register_map,
    parse_register_range,
    word_swapped_u32,
)

_WALL_CLOCK = 1_800_000_000.0
_MAX = profile_max_airflow("ii_fc_voc")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _model(
    units: dict[int, str] | None = None, *, warmup_seconds: float = 0, settle_seconds: float = 0
) -> tuple[MockGatewayModel, _Clock]:
    clock = _Clock()
    model = MockGatewayModel(
        units or {2: "ii_fc_voc"},
        warmup_seconds=warmup_seconds,
        settle_seconds=settle_seconds,
        clock=clock,
        wall_clock=lambda: _WALL_CLOCK,
    )
    return model, clock


def _write_all(model: MockGatewayModel, writes: tuple[tuple[int, int], ...]) -> None:
    for register, value in (*writes, (REGISTER_APPLY, 0)):
        assert model.write(2, register, value) is None


def _status_and_airflow(model: MockGatewayModel) -> tuple[list[int], list[int]]:
    status = model.read(2, REGISTER_MODE_STATUS, 3)
    airflow = model.read(2, REGISTER_EXTRACT_AIR_FLOW, 2)
    assert isinstance(status, list)
    assert isinstance(airflow, list)
    return status, airflow


def test_reads_outside_the_readable_islands_fail_as_a_whole() -> None:
    model, _ = _model()

    assert isinstance(model.read(2, 41000, 14), list)
    assert model.read(2, 41035, 1) == ILLEGAL_DATA_ADDRESS
    # 40023 lies between two islands, so the whole block fails.
    assert model.read(2, 40020, 5) == ILLEGAL_DATA_ADDRESS
    assert model.read(2, 41000, 126) == ILLEGAL_DATA_VALUE
    assert model.read(1, 41998, 4) == ILLEGAL_DATA_ADDRESS
    assert model.read(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 2) == [1, 2]
    assert model.write(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 3) == ILLEGAL_DATA_ADDRESS


def test_set_registers_acknowledge_until_written() -> None:
    model, _ = _model()
    assert model.read(2, REGISTER_CURRENT_LEVEL, 1) == ACKNOWLEDGE

    _write_all(model, ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 100)))

    assert model.read(2, REGISTER_CURRENT_LEVEL, 1) == [100]
    assert model.read(2, REGISTER_MODE, 2) == [3, 100]
    # The extract target and the intensive registers were not written.
    assert model.read(2, REGISTER_MODE, 5) == ACKNOWLEDGE
    assert model.read(2, REGISTER_APPLY, 1) == ILLEGAL_DATA_ADDRESS
    assert model.readable_set_registers(2) == {REGISTER_MODE, REGISTER_CURRENT_LEVEL}

    model.reboot()
    assert model.read(2, REGISTER_CURRENT_LEVEL, 1) == ACKNOWLEDGE


def test_warmup_acknowledges_the_sensor_blocks_until_it_ends() -> None:
    model, clock = _model(warmup_seconds=60)

    assert model.read(2, 41000, 6) == ACKNOWLEDGE
    assert model.read(2, REGISTER_EXTRACT_AIR_FLOW, 2) == ACKNOWLEDGE
    # Other blocks answered during the warm-up on hardware.
    assert isinstance(model.read(2, 41007, 2), list)
    assert isinstance(model.read(2, 40100, 5), list)
    assert model.warmup_remaining == 60

    clock.now += 60
    assert isinstance(model.read(2, 41000, 6), list)

    model.start_warmup(10)
    assert model.read(2, 41000, 6) == ACKNOWLEDGE
    with pytest.raises(ValueError):
        model.start_warmup(-1)


@pytest.mark.parametrize(
    ("writes", "status", "airflow"),
    [
        pytest.param(
            ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 100)),
            [3, 100, 100],
            [_MAX // 2, _MAX // 2],
            id="balanced",
        ),
        pytest.param(
            (
                (REGISTER_MODE, 4),
                (REGISTER_CURRENT_LEVEL, 60),
                (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 120),
            ),
            [4, 60, 120],
            [round(120 * _MAX / 200), round(60 * _MAX / 200)],
            id="unbalanced",
        ),
        pytest.param(
            (
                (REGISTER_MODE, 4),
                (REGISTER_CURRENT_LEVEL, 0),
                (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 204),
            ),
            [4, 0, 204],
            [40, 0],
            id="app-extract-preset",
        ),
        pytest.param(
            ((REGISTER_MODE, 2), (REGISTER_CURRENT_LEVEL, 144)),
            [2, 176, 176],
            [40, 40],
            id="co2-control",
        ),
        pytest.param(
            ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 229)),
            [3, 229, 229],
            [30, 30],
            id="quick-medium",
        ),
        pytest.param(
            ((REGISTER_MODE, 1), (REGISTER_CURRENT_LEVEL, 0)),
            [1, 0, 0],
            [0, 0],
            id="off",
        ),
    ],
)
def test_commits_update_status_and_airflow(
    writes: tuple[tuple[int, int], ...], status: list[int], airflow: list[int]
) -> None:
    model, _ = _model()

    _write_all(model, writes)

    assert _status_and_airflow(model) == (status, airflow)


def test_intensive_runs_on_until_the_shadow_registers_are_cleared() -> None:
    model, _ = _model()

    _write_all(model, ((REGISTER_PRESET_MODE, 3), (REGISTER_PRESET_VALUE, 227)))
    assert _status_and_airflow(model) == ([3, 227, 227], [80, 80])

    _write_all(model, ((REGISTER_MODE, 1), (REGISTER_CURRENT_LEVEL, 0)))
    assert _status_and_airflow(model) == ([3, 227, 227], [80, 80])

    _write_all(model, ((REGISTER_PRESET_MODE, 0), (REGISTER_PRESET_VALUE, 0)))
    assert _status_and_airflow(model) == ([1, 0, 0], [0, 0])


def test_settle_delays_status_and_airflow_but_not_the_set_registers() -> None:
    model, clock = _model(settle_seconds=2)

    _write_all(model, ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 100)))

    assert _status_and_airflow(model) == ([3, 80, 80], [40, 40])
    assert model.read(2, REGISTER_CURRENT_LEVEL, 1) == [100]
    assert "settling for 2.0 s" in model.describe()
    clock.now += 2
    assert _status_and_airflow(model) == ([3, 100, 100], [_MAX // 2, _MAX // 2])


def test_a_reboot_completes_a_pending_commit() -> None:
    model, _ = _model(settle_seconds=60)
    _write_all(model, ((REGISTER_MODE, 1), (REGISTER_CURRENT_LEVEL, 0)))

    model.reboot()

    assert _status_and_airflow(model) == ([1, 0, 0], [0, 0])


@pytest.mark.parametrize(
    ("change", "status", "airflow"),
    [
        pytest.param(("medium",), [3, 229, 229], [30, 30], id="medium"),
        pytest.param(("intensive",), [0, 0, 0], [80, 80], id="intensive"),
        pytest.param(("off",), [1, 0, 0], [0, 0], id="off"),
        pytest.param(("60",), [3, 120, 120], [60, 60], id="balanced"),
        pytest.param(("40", "60"), [4, 80, 120], [60, 40], id="unbalanced"),
    ],
)
def test_app_changes_bypass_the_set_registers(
    change: tuple[str, ...], status: list[int], airflow: list[int]
) -> None:
    model, _ = _model({2: "ii_plain"})
    assert profile_max_airflow("ii_plain") == 100

    model.app_change(2, *change)

    assert _status_and_airflow(model) == (status, airflow)
    assert model.read(2, REGISTER_CURRENT_LEVEL, 1) == ACKNOWLEDGE


@pytest.mark.parametrize("change", [("500",), ("fast",), ("60", "-1")])
def test_app_changes_reject_invalid_airflow(change: tuple[str, ...]) -> None:
    model, _ = _model({2: "ii_plain"})

    with pytest.raises(ValueError):
        model.app_change(2, *change)


def test_pairing_updates_the_gateway_unit_list() -> None:
    model, _ = _model({2: "ii_plain"})

    model.pair(4, "ii_f")
    assert model.read(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 3) == [2, 2, 4]
    assert model.answers(4)

    model.unpair(2)
    assert model.read(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 3) == [1, 4, 0]
    assert not model.answers(2)
    model.pair(2, "ii_plain")
    # A unit paired again is listed last, as the real list follows pairing order.
    assert model.paired == [4, 2]

    model.set_silent(4, True)
    assert not model.answers(4)
    model.set_silent(4, False)
    assert model.answers(4)

    for invalid in ((17, "ii_f"), (5, "nope")):
        with pytest.raises(ValueError):
            model.pair(*invalid)
    with pytest.raises(ValueError):
        model.unpair(9)
    with pytest.raises(ValueError):
        model.set_silent(1, True)


def test_pairing_with_another_profile_rebuilds_the_unit() -> None:
    model, _ = _model({2: "ii_plain"})

    model.pair(2, "ii_f")

    assert model.units == {2: "ii_f"}
    assert model.peek(2, REGISTER_HUMIDITY_EXTRACT_AIR) == [48]


def test_uptime_and_utc_clock_follow_the_clocks() -> None:
    model, clock = _model()

    assert model.read(1, REGISTER_GATEWAY_UPTIME, 2) == list(
        word_swapped_u32(REGISTER_GATEWAY_UPTIME, INITIAL_UPTIME_SECONDS).values()
    )
    clock.now += 10
    assert model.peek(1, REGISTER_GATEWAY_UPTIME, 2) == list(
        word_swapped_u32(REGISTER_GATEWAY_UPTIME, INITIAL_UPTIME_SECONDS + 10).values()
    )
    model.clock_offset = -30
    assert model.read(1, REGISTER_GATEWAY_UTC_CLOCK, 2) == list(
        word_swapped_u32(REGISTER_GATEWAY_UTC_CLOCK, int(_WALL_CLOCK) - 30).values()
    )

    model.reboot()
    clock.now += 5
    assert model.read(1, REGISTER_GATEWAY_UPTIME, 2) == [5, 0]


def test_flags_and_refused_registers() -> None:
    model, _ = _model()

    model.set_flag(2, "filter", True)
    assert model.read(2, REGISTER_FILTER_CHANGE_DUE, 1) == [1]
    with pytest.raises(ValueError):
        model.set_flag(2, "smoke", True)

    model.refuse(2, (41007, 41007))
    assert model.read(2, 41006, 6) == ILLEGAL_DATA_ADDRESS
    assert model.write(2, 41007, 1) == ILLEGAL_DATA_ADDRESS
    assert model.refused(2) == {41007}
    model.refuse(2, (41007, 41007), refused=False)
    assert isinstance(model.read(2, 41006, 6), list)

    model.refuse(1, (REGISTER_GATEWAY_UPTIME, REGISTER_GATEWAY_UPTIME + 1))
    assert model.read(1, REGISTER_GATEWAY_UPTIME, 2) == ILLEGAL_DATA_ADDRESS
    assert "gateway refuses 41019-41020" in model.describe()


def test_set_register_makes_other_addresses_readable() -> None:
    model, _ = _model()

    model.set_register(2, 51100, 4354)

    assert model.read(2, 51100, 1) == [4354]
    with pytest.raises(ValueError):
        model.set_register(2, 41006, 70000)


def test_describe_summarises_units_and_overrides() -> None:
    model, _ = _model({2: "ii_fc_voc", 3: "ii_plain"}, warmup_seconds=30)
    model.unpair(3)
    model.set_silent(2, True)
    model.refuse(2, (41007, 41008))
    _write_all(model, ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 100)))

    summary = model.describe()

    assert "warm-up 30 s left" in summary
    assert "units listed [2]" in summary
    assert "unit 2: ii_fc_voc (paired, silent), status 3/100/100" in summary
    assert "readable set registers 41120-41121" in summary
    assert "refuses 41007-41008" in summary
    assert "unit 3: ii_plain (not paired)" in summary


def test_captures_override_registers_and_readability() -> None:
    model, _ = _model()

    model.apply_register_map(
        {
            "metadata": {"slave": 2, "label": "capture"},
            "values": {"41006": 50, "41007": None, "41120": None, "41132": None, "51100": 4354},
        }
    )

    assert model.read(2, 41006, 1) == [50]
    assert model.read(2, 41007, 1) == ILLEGAL_DATA_ADDRESS
    assert model.read(2, 51100, 1) == [4354]
    # Unreadable set registers in a capture only mean "not written yet".
    assert model.read(2, REGISTER_MODE, 1) == ACKNOWLEDGE
    assert model.refused(2) == {41007}


def test_register_maps_round_trip() -> None:
    source, _ = _model({2: "ii_fc_voc", 3: "ii_plain"})
    source.pair(4, "ii_f")
    source.unpair(3)
    source.set_register(2, 41006, 55)
    source.refuse(2, (41012, 41012))
    data = json.loads(json.dumps(source.to_register_map()))

    target, _ = _model({2: "ii_plain"})
    target.apply_register_map(data)

    assert data["format"] == 1
    assert target.units == source.units
    assert sorted(target.paired) == sorted(source.paired)
    assert target.peek(2, 41006) == [55]
    assert target.refused(2) == {41012}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"format": 99}, id="format"),
        pytest.param({"format": 1, "units": []}, id="units-type"),
        pytest.param({"format": 1, "gateway": []}, id="gateway-type"),
        pytest.param({"format": 1, "units": {"4": {}}}, id="missing-profile"),
        pytest.param({"format": 1, "units": {"20": {"profile": "ii_f"}}}, id="address"),
        pytest.param({"format": 1, "units": {"4": {"profile": "nope"}}}, id="profile"),
        pytest.param({"format": 1, "units": {"2": []}}, id="unit-type"),
        pytest.param({"format": 1, "units": {"2": {"registers": []}}}, id="registers-type"),
        pytest.param({"format": 1, "units": {"2": {"registers": {"x": 1}}}}, id="register"),
        pytest.param({"format": 1, "units": {"2": {"registers": {"70000": 1}}}}, id="range"),
        pytest.param({"format": 1, "units": {"2": {"registers": {"41006": 1.5}}}}, id="value"),
        pytest.param({"format": 1, "units": {"2": {"registers": {"41006": True}}}}, id="bool"),
        pytest.param({"metadata": {"slave": 5}, "values": {}}, id="capture-unit"),
        pytest.param({"metadata": [], "values": {}}, id="capture-metadata"),
    ],
)
def test_invalid_register_maps_are_rejected(data: dict[str, object]) -> None:
    model, _ = _model()

    with pytest.raises(RegisterMapError):
        model.apply_register_map(data)


def test_load_register_map_reports_unreadable_files(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    with pytest.raises(RegisterMapError, match="cannot read"):
        load_register_map(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RegisterMapError, match="JSON object"):
        load_register_map(path)
    path.write_text('{"format": 1}', encoding="utf-8")
    assert load_register_map(path) == {"format": 1}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("41007", (41007, 41007)), ("41007-41008", (41007, 41008))],
)
def test_parse_register_range(text: str, expected: tuple[int, int]) -> None:
    assert parse_register_range(text) == expected


@pytest.mark.parametrize("text", ["x", "41008-41007", "70000", "-1"])
def test_parse_register_range_rejects_invalid_ranges(text: str) -> None:
    with pytest.raises(ValueError):
        parse_register_range(text)


def test_invalid_model_arguments_are_rejected() -> None:
    with pytest.raises(ValueError):
        MockGatewayModel({2: "ii_plain"}, warmup_seconds=-1)
    with pytest.raises(ValueError):
        MockGatewayModel({2: "ii_plain"}, silent_units=[3])
    with pytest.raises(ValueError):
        MockGatewayModel({1: "ii_plain"})
    model, _ = _model()
    with pytest.raises(ValueError):
        model.reboot(-1)
    with pytest.raises(ValueError):
        model.check_address(9)
