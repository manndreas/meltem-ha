# Hypothesis-driven architecture and code review: `meltem-ha` (v4.2.0)

Review date: **2026-10-16**
Reviewed state: version `4.2.0`

## Status of the findings (as of 2026-11-02, version `5.0.0`)

The evidence below describes the state of `4.2.0`; line numbers there apply only to that state.

| Finding | Status | Resolution |
| --- | --- | --- |
| P1-1 | fixed | Failed groups of a job count as errors, even when the client swallows the Modbus errors (`read_health.job_read_failure`, `job_read_unanswered`). |
| P1-2 | fixed | Overlays remember the read health of their groups at write time; only a later read confirms (`overlay.read_since_set`). |
| P1-3 | fixed | The exact readback value of `41121` is carried over further airflow polls (`write_confirmation.prepare_balanced_target_readback`). |
| P1-4 | fixed | `levels.target_levels` no longer copies the supply air to the extract air, and `level_source` stays `measured` in quick-select mode. |
| P2-1 | fixed | `_fresh_values` discards sentinels via `PLAUSIBLE_RANGES`; since `5.0.0` the field becomes `unknown` instead of frozen, and a repair issue reports a permanently missing sensor. |
| P2-2 | fixed | `MeltemRoomDevice` restricts the air quality components to the profile; the device cache is keyed by address and profile. |
| P2-3 | open | The meaning of values above `200` in the target registers cannot be clarified without a measurement; tracked as HW-3 in the [hardware backlog](../HARDWARE_BACKLOG.md#hw-3--ambiguous-encoding-above-200-in-the-unbalanced-target-registers). |
| P3-1 | fixed | The diagnostics download reads the transport counters before the live discovery read. |
| P3-2 | fixed | Since `5.0.0`, mode writes read back exactly once after `0.5 s`; the retry loop including `POST_WRITE_REFRESH_*` has been removed. The measurement for this is under [HW-2](../HARDWARE_BACKLOG.md#decision-and-validation-on-2026-11-02). |
| P3-3 | fixed | Profiles are changed per device via "Reconfigure", without a probe; the former options step has been removed. |
| P3-4 | fixed | An aborted setup cleans up the coordinator and client; since `5.0.0` a new request rate also reschedules an already planned tick (own scheduler). |
| P3-5 | fixed | A later regular write clears the fallback notice; device registry updates run only when the versions change. |
| P4 | fixed | The fake clients reproduce the errors of the real client; the platform tests use `create_autospec` of the coordinator; snapshot tests cover entities and diagnostics. |
| P5 | largely | See [INTEGRATION_QUALITY_SCALE.md](../INTEGRATION_QUALITY_SCALE.md). |

---

This document contains all findings of the bottom-up architecture and code review, from the raw data on the wire ([`docs/dev/measurements/`](../measurements/) and [`tools/`](../../../tools/)) through the protocol and coordination layer up to the Home Assistant platforms, the test suite ([`tests/`](../../../tests/)), and the documentation.

The findings are strictly prioritized by severity:

- **P1:** Broken state machines, race conditions & data corruption
- **P2:** Protocol and hardware mismatches
- **P3:** Event loop, lock, and lifecycle risks
- **P4:** Mock tautologies & false confidence in the test suite
- **P5:** Home Assistant Quality Scale (IQS), schema, and documentation consistency

---

## 0. Synthesis of the raw data on the wire (`docs/dev/measurements/` & `tools/`)

The real gateway captures in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt), [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt), [`pe-compare-slave5.txt`](../measurements/2026-10-09/pe-compare-slave5.txt), [`pe-identity-slave4.txt`](../measurements/2026-10-09/pe-identity-slave4.txt), [`pe-identity-slave5.txt`](../measurements/2026-10-09/pe-identity-slave5.txt), [`pe-product-40009-40010-slaves2-3-6-7.txt`](../measurements/2026-10-09/pe-product-40009-40010-slaves2-3-6-7.txt) and the JSON captures ([`intensive-duration-slave2-before-15m.json`](../measurements/2026-10-09/intensive-duration-slave2-before-15m.json), [`intensive-duration-slave2-after-20m.json`](../measurements/2026-10-09/intensive-duration-slave2-after-20m.json), [`intensive-duration-slave2-restored-15m.json`](../measurements/2026-10-09/intensive-duration-slave2-restored-15m.json)) yield the following constraints of the `M-WRG-GW` bridge (Honeywell/Airios 868.3 MHz RF bridge over USB RTU `19200 8E1`):

1. **Response times & pacing:** Isolated register reads take `~15.5–16.9 ms`; consecutive reads with `REQUEST_GAP_SECONDS = 0.1` (`100 ms`) are constantly at `~115.9–116.1 ms` ([`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt)). With concurrent write/read traffic, the read latency rises to up to `134.4 ms`.
2. **Block read atomicity:** A `0x03` block read fails completely with Modbus exception `0x02` (`IllegalDataAddressError`) or `0x05` (`AcknowledgeError`) as soon as **a single** register in the window is unreadable (which is why `window=10` in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt) from line 203 on hides almost all register islands).
3. **Sentinel values of sensors that are not fitted:** On base units (`ii_plain`, slaves 2–7 in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt)), sensors that are not fitted do **not** answer with Modbus exceptions but with fixed sentinel raw values:
   - Missing Float32 temperature sensors (`41000..41001`, `41002..41003`, `41009..41010`): `[0, 32704]` (`0x7FC00000` = IEEE-754 `NaN`).
   - Present exhaust air sensor (`41004..41005`): e.g. `[2622, 16785]` = `18.13 °C` (little-endian word order).
   - Missing humidity sensors (`41006`, `41011`): **`239`** (`0x00EF`).
   - Missing CO2/VOC sensors (`41007`, `41013`) and unused registers (`41008`, `41012`): **`32767`** (`0x7FFF`).
4. **Asymmetry between the status map (`41100..41102`) and the set registers (`41120..41124`):**
   - `41120..41124` and `41132` are unreadable before a first write (`null` or `0x05` in [`intensive-duration-slave2-before-15m.json`](../measurements/2026-10-09/intensive-duration-slave2-before-15m.json)).
   - `41100..41102` is always readable, but for balanced target values in `41101` it often returns the coarse app encoding `200 + airflow / 10` (steps of 10: `204` = 40 m³/h, `206` = 60 m³/h) or quick-select codes (`228..230`), and in unbalanced operation (`41100 = 4`) on slave 4 even `[4, 0, 228]` ([`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt)).

---

## 1. Prioritized findings with evidence

### [P1-1] Broken error and availability state machine: `MeltemModbusClient.read_room_state` swallows all device timeouts and Modbus exceptions, so `_room_failures`, `_last_job_error`, and the transport backoff are dead in real operation

- **Severity:** Critical (broken state machine / stale-state detection & transport backoff disabled)
- **(a) Exact file and line references:**
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient.read_room_state` (lines 276–302), `MeltemModbusClient._poll` (lines 321–351), `MeltemModbusClient._read_mode_status` (lines 467–508)
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator._room_unavailable_reason` (lines 261–273), `MeltemDataUpdateCoordinator._async_update_data` (lines 365–406), `MeltemDataUpdateCoordinator._read_one_job` (lines 1130–1166)
  - [`device/transport.py`](../../../custom_components/meltem_ventilation/device/transport.py): `TransportPolicy.run` (lines 101–147)
- **(b) Triggering call and event sequence:**
  1. In `MeltemDataUpdateCoordinator._read_one_job` (lines 1138–1155), the coordinator expects a failed poll job to raise `MeltemModbusError` in order to set `self._last_job_error = err`, increment `self._room_failures[room.key]` (threshold `ROOM_UNAVAILABLE_AFTER_FAILURES = 3`), and call `self._on_transport_failure()` in `MeltemDataUpdateCoordinator._async_update_data` (lines 394–397) for the exponential gateway backoff (`5 s .. 60 s`).
  2. In fact, however, `MeltemModbusClient._poll` catches `ModbusTimeoutError` (`except ModbusTimeoutError as err:` in line 334), and `device.async_poll()` from `modbus_connection` catches all other `ModbusError` subclasses (`AcknowledgeError` `0x05`, `GatewayTargetError` `0x0B`, `GatewayPathUnavailableError` `0x0A`, `ModbusProtocolError`) and merely puts them into `report.failed`. Likewise, `MeltemModbusClient._read_mode_status` in lines 495–505 fully catches `ModbusTimeoutError`, `ModbusExceptionError`, and `ModbusError`.
  3. As a result, `MeltemModbusClient.read_room_state` returns normally without an exception on **every** timeout, **every** CRC/protocol error, and **every** Modbus exception (`0x05`, `0x0A`, `0x0B`) (only `ModbusConnectionError` when the USB plug is physically pulled raises `MeltemConnectionError`).
  4. In `MeltemDataUpdateCoordinator._read_one_job`, the `else:` branch (lines 1156–1160) is therefore always taken:
     - `self._last_job_error` is set to `None` (diagnostics and System Health show `"last_job_error": "none"`, even when all devices return timeouts or `0x05`).
     - `self._room_failures.pop(room.key, None)` clears the failure counter on every failed job (on timeouts/Modbus errors, `_room_failures` **never** reaches `ROOM_UNAVAILABLE_AFTER_FAILURES = 3`).
     - In `MeltemDataUpdateCoordinator._async_update_data` (line 394), `self._last_job_error is None`, so with a completely silent/hanging gateway after startup, `self._on_transport_success()` instead of `self._on_transport_failure()` is called on every timeout job – the transport backoff never kicks in!
  5. **Aggravated by gateway register `40101` (`rf_comm_status`):** If after a gateway reconnect (as in the real H-3 test, documented in [`HARDWARE_BACKLOG.md`](../HARDWARE_BACKLOG.md) lines 575–578 and 629–630) all device blocks (`41020`, `41004`, `41016`) return exception `0x05` for minutes, but the bridge register `40101` (`rf_comm_status`) answers in the 60-second `status` job, `TransportPolicy.run` (line 145) sets `self._last_read_answer[unit_id] = time.monotonic()` every 60 s. Since `60 s < ROOM_SILENT_AFTER_SECONDS (120 s)` and `_room_failures` always stays `0`, the device is **never** marked `unavailable`, and control entities (`MeltemDirectionalFanEntity`, `MeltemOperationModeSelect`, `MeltemPresetModeSelect`, `MeltemIntensiveSwitch` with `_requires_fresh_read_group = False`) permanently stay `available = True`.
- **(c) Reproducing `pytest` proof (with the real `MeltemModbusClient` instead of `_FakeClient`):**

```python
async def test_real_client_timeouts_never_trigger_room_failures_or_backoff(
    hass: HomeAssistant,
) -> None:
    link = MockModbusConnection()
    unit = link.for_unit(2)
    unit.holding.update({41020: [30, 30], 41100: [3, 60, 0]})
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    client = MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
    entry.add_to_hass(hass)
    coordinator = MeltemDataUpdateCoordinator(
        hass, config_entry=entry, client=client, rooms=[room], max_requests_per_second=10.0
    )

    # 1. First poll succeeds -> room has cached data
    await coordinator.async_refresh()
    assert coordinator.room_available("unit_1") is True

    # 2. From now on ALL reads on slave 2 fail with a timeout (or 0x05)
    unit.fail_requests(ModbusTimeoutError("unit silent"))
    for _ in range(5):
        for job in coordinator._jobs:
            job.next_due = 0.0
        coordinator._last_read_started = None
        await coordinator._async_update_data()

    # BUG: Despite 5 failed jobs in a row:
    assert coordinator._room_failures.get("unit_1", 0) == 0  # Should be >= 3!
    assert coordinator.last_job_error is None                # Should contain an error!
    assert coordinator._backoff_seconds is None              # No backoff despite total outage!
    assert coordinator.room_available("unit_1") is True      # Room wrongly stays available!
```

---

### [P1-2] `OptimisticOverlay` has no timestamp check against `last_successful_read`: previously cached states and neighbouring room polls clear fresh write overlays prematurely

- **Severity:** High (race condition / ghost reverts in the UI)
- **(a) Exact file and line references:**
  - [`overlay.py`](../../../custom_components/meltem_ventilation/overlay.py): `OptimisticOverlay.set` (lines 37–50), `OptimisticOverlay.settle` (lines 57–65), `OptimisticOverlay.get` (lines 67–78)
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.optimistic_preset_mode` (lines 295–306), `MeltemDataUpdateCoordinator.optimistic_intensive` (lines 308–317), `MeltemDataUpdateCoordinator._resolve_levels` (lines 333–341), `MeltemDataUpdateCoordinator._confirm_pending_writes` (lines 1110–1128)
  - [`levels.py`](../../../custom_components/meltem_ventilation/levels.py): `LEVEL_CONFIRM_TOLERANCE = 2` (line 13), `levels_reached` (lines 23–29)
- **(b) Triggering call and event sequence:**
  While `_checked_confirmation` in [`write_confirmation.py`](../../../custom_components/meltem_ventilation/write_confirmation.py) (line 194) explicitly checks whether the read took place after the write command (`read_health.last_attempt < confirmation.started_at`), `OptimisticOverlay` stores no `started_at` timestamp and blindly compares the pending write value against the state currently held in `self.safe_data[room_key]` (read before the write!). This leads to two concrete errors:
  1. **Immediate loss of ±1 % / ±2 % write commands:**
     - At time `t = 0 s`, `unit_1` was read with `operation_mode="manual", target_level=60` (`flow_control` is "fresh" for 180 s).
     - At `t = 1 s`, the user changes the fan in Home Assistant from `60 %` to `62 %` (or `61 %`, `59 %`, `58 %`).
     - `MeltemDataUpdateCoordinator.async_set_direction_level` writes `(62, 60)` to the gateway (`refresh_attempts=0`, i.e. no immediate readback) and sets `self._optimistic_levels.set("unit_1", (62, 60))`.
     - On the immediate `self.async_update_listeners()`, the fan entity calls `MeltemDataUpdateCoordinator._resolve_levels`. Since `self.safe_data["unit_1"]` still holds the old value `(60, 60)` from `t = 0 s` and `levels_reached` returns `True` because `abs(60 - 62) <= LEVEL_CONFIRM_TOLERANCE (2)`, `OptimisticOverlay.get` immediately returns `None`! The UI jumps back to `(60, 60)` and `level_source: "target"` within the same event loop tick.
  2. **Fast A→B→A correction + poll of another room (ghost update):**
     - `t = 0 s`: `unit_1` is at `60 %` (`RoomState` from `t = 0 s` has `target_level=60`).
     - `t = 1 s`: The user sets `unit_1` to `40 %` (`refresh_attempts=0`, `self.safe_data["unit_1"]` stays at `target_level=60` from `t = 0 s`).
     - `t = 2 s`: The user corrects `unit_1` back to `60 %`.
     - `t = 2.25 s`: A regular poll job for **`unit_2`** (or a `temperature` job for `unit_1`) runs in `MeltemDataUpdateCoordinator._async_update_data` and calls `self._confirm_pending_writes(updated_data)` for **all** rooms (line 1116).
     - `self._optimistic_levels.settle("unit_1", (60, 60))` sees the pre-write cache `(60, 60)` from `t = 0 s` and **clears the overlay of `unit_1`**!
     - `t = 3 s`: The first real `flow` poll for `unit_1` arrives while the device has only applied the first command (`40 %`) over RF so far. Because the overlay was already cleared at `t = 2.25 s`, the UI jumps back from `60 %` to `40 %`! The same happens analogously with `_optimistic_presets` and `_optimistic_intensive`, because `read_health.group_fresh()` only checks whether the last read is younger than 180 s, not whether it happened **after** `overlay.set()`.
- **(c) Reproducing `pytest` proof:**

```python
async def test_small_step_and_aba_write_overlay_prematurely_settled_by_pre_write_cache(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _build_coordinator(hass, [_UNIT_1])
    coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))

    # Case 1: A change of 2 m³/h (60 -> 62) is swallowed immediately by the old cache (60)
    await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 62)
    assert coordinator.effective_levels("unit_1") == (60, 60)  # BUG: Should be (62, 60)!
    assert coordinator.level_source("unit_1") == "target"      # BUG: Should be "pending"!

    # Case 2: A (60) -> B (40) -> A (60), followed by a poll of another job
    await coordinator.async_set_level("unit_1", 40)
    await coordinator.async_set_level("unit_1", 60)
    coordinator._confirm_pending_writes(coordinator.safe_data)  # old state from t=0!
    assert "unit_1" not in coordinator._optimistic_levels._pending  # BUG: overlay cleared!
```

---

### [P1-3] `state.balanced_target_readback` (`41121`) is completely ignored by `levels.py` and cleared on the next poll – loss of all ventilation levels not divisible by 10 after 15 seconds

- **Severity:** High (wrong state display & creeping setpoint corruption on subsequent writes)
- **(a) Exact file and line references:**
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator._read_balanced_target_if_pending` (lines 999–1041)
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient._read_mode_group` (lines 543–603, esp. lines 579–590)
  - [`levels.py`](../../../custom_components/meltem_ventilation/levels.py): `target_levels` (lines 44–78, esp. lines 71–78)
  - [`write_confirmation.py`](../../../custom_components/meltem_ventilation/write_confirmation.py): `_readback_value` (lines 220–240, esp. lines 221–225)
- **(b) Triggering call and event sequence & contradiction with the hardware measurements:**
  1. According to live measurement W-1 ([`MELTEM.md`](../MELTEM.md) lines 273–276, [`HARDWARE_BACKLOG.md`](../HARDWARE_BACKLOG.md) lines 214–239), the status register `41101` reports the app encoding `200 + airflow / 10` in balanced operation (grid of 10: `204` = 40 m³/h, `206` = 60 m³/h), while the set register `41121` holds the exact raw value (`0..200`, 1 m³/h resolution).
  2. If the user sets both fans to `65 %` (`65 m³/h`, `41121 = 130`), the next `flow` poll on `41100..41102` returns `[3, 206, 0]`. `MeltemModbusClient._read_mode_group` decodes `41101 = 206` via `_decode_balanced_target_readback` to **`state.target_level = 60`** (and, because `target is not None`, also does **not** fall back to `derive_balanced_airflow`, even when `41020/41021` already report `[65, 65]`!).
  3. `MeltemDataUpdateCoordinator._read_balanced_target_if_pending` now reads `41121 = 130` and stores `65` in `state.balanced_target_readback = 65`.
  4. `_readback_value` thereby confirms the write (`status = "confirmed"`).
  5. **But:** `target_levels` in [`levels.py`](../../../custom_components/meltem_ventilation/levels.py) completely ignores `state.balanced_target_readback` and in line 72 only reads `common = state.target_level` (`60`). In addition, `balanced_target_readback` is overwritten with `None` again on the next poll in `MeltemModbusClient._read_mode_group` (line 589), because `self._writes.needs_confirmation(room.key, "airflow_levels")` is `False` after `"confirmed"`.
  6. As soon as `TARGET_OPTIMISTIC_SECONDS` (`15 s`) expires, both fan entities in Home Assistant jump back from `65 %` to `60 %` – and if the user afterwards changes only the extract air to `40 %`, `MeltemDataUpdateCoordinator.async_set_direction_level` reads `supply = 60` instead of `65` as the opposite direction and unintentionally overwrites the supply air with `60 m³/h`!
- **(c) Reproducing `pytest` proof:**

```python
async def test_balanced_non_multiple_of_10_reverts_to_coarse_status_target(
    hass: HomeAssistant,
) -> None:
    link = MockModbusConnection()
    unit = link.for_unit(2)
    # Unit runs at 65 m³/h (41121=130, 41020..41021=[65, 65]), status 41101 reports 206 (60 m³/h)
    unit.holding.update({41020: [65, 65], 41100: [3, 206, 0], 41121: [130]})
    client = MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")
    coordinator = MeltemDataUpdateCoordinator(
        hass, config_entry=_mock_entry(hass), client=client, rooms=[_UNIT_1], max_requests_per_second=10.0
    )
    coordinator._writes.record_pending("unit_1", "airflow_levels", (65, 65))

    await coordinator.async_refresh()

    # WriteConfirmation is "confirmed" (65, 65), but effective_levels returns (60, 60)!
    assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "confirmed"
    assert coordinator.effective_levels("unit_1") == (60, 60)  # BUG: Should be (65, 65)!
```

---

### [P1-4] Contradiction between `_decode_balanced_target_readback` and `levels.target_levels`: wrong `level_source="target"` in quick-select mode and copying of `supply_air_flow` onto `extract_air_flow` during frost protection / asymmetry

- **Severity:** High (data corruption of the displayed fan values and `level_source` attributes)
- **(a) Exact file and line references:**
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `_decode_balanced_target_readback` (lines 750–764)
  - [`levels.py`](../../../custom_components/meltem_ventilation/levels.py): `target_levels` (lines 44–78, esp. lines 71–78), `reported_levels` (lines 81–97, esp. lines 91–95)
- **(b) Triggering call and event sequence:**
  1. **Corruption of `level_source`:** When a device runs in quick-select mode (e.g. `[3, 229, 0]` = `medium`, as on slaves 2, 3, 7 in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt)), the target value is not readable over Modbus (`229` is the preset code). `_decode_balanced_target_readback` therefore falls back in line 764 to `derive_balanced_airflow(extract_air_flow, supply_air_flow)` and stores this **measured value** in `state.target_level`!
     In `reported_levels` (lines 92–94), `targets = target_levels(state, airflow_is_fresh=False)` is called to check whether the values come from a target register (`"target"`) or from the measurement (`"measured"`). Because `state.target_level` has already been overwritten with the measured value, `levels == targets` is always `True`, and `reported_levels` wrongly reports `level_source = "target"` instead of `"measured"`. Moreover, this derived value stays active even when the `flow` group is stale (`airflow_is_fresh == False`)!
  2. **Extract air overwritten with the supply air when `abs(extract - supply) > 1` (e.g. frost protection or ramping):**
     If in `manual` mode (`41100 = 3`, e.g. preset `229` or a failed status read) `extract_air_flow = 40` and `supply_air_flow = 0` (as documented for frost protection operation in [`functions.md`](../reference/functions.md) line 181!), `derive_balanced_airflow` deliberately returns `None` (`state.target_level = None`).
     Afterwards, however, `target_levels` executes the following code in lines 72–77:

     ```python
     common = state.target_level
     if common is None and airflow_is_fresh:
         common = state.supply_air_flow
     if common is None and airflow_is_fresh:
         common = state.extract_air_flow
     return common, common
     ```

     Since `common` is set to `state.supply_air_flow` (`0`), `state.extract_air_flow` (`40`) is discarded completely and `(0, 0)` is returned for **both** fans! The extract air fan is shown in Home Assistant as `off` (`0 %`), although it actually runs at `40 m³/h`.
- **(c) Reproducing `pytest` proof:**

```python
def test_target_levels_overwrites_extract_with_supply_when_diverging() -> None:
    now = dt_util.utcnow()
    health = ReadHealth(last_attempt=now, last_successful_read=now)
    # In "manual" mode (preset 229 -> target_level=None) during frost protection: supply 0, extract 40
    state = RoomState(
        operation_mode="manual",
        preset_mode="medium",
        target_level=None,
        supply_air_flow=0,
        extract_air_flow=40,
        group_read_health=(("flow", health), ("flow_control", health)),
    )
    # BUG: returns ((0, 0), "measured") instead of ((0, 40), "measured")!
    assert reported_levels(state) == ((0, 0), "measured")
```

---

### [P2-1] Unfiltered hardware sentinel values (`239 %` RH, `32767 ppm` CO2/VOC) in the runtime path `_fresh_values`

- **Severity:** High (protocol deviation / corruption of the sensor time series and long-term statistics)
- **(a) Exact file and line references:**
  - [`modbus_helpers.py`](../../../custom_components/meltem_ventilation/modbus_helpers.py): `_PLAUSIBLE_RANGES` (lines 55–60), `_is_plausible` (lines 317–319)
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `_fresh_values` (lines 686–700, esp. lines 695–698)
  - Measurement data: [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt) (lines 71–78, 167–174), [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (lines 13–14)
- **(b) Triggering input data & contradiction with the measurement data:**
  1. The hardware scans in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt) show for the UINT16 sensor registers `41006` (`humidity_extract_air`), `41011` (`humidity_supply_air`), `41007` (`co2_extract_air`), and `41013` (`voc_supply_air`) that the device raises **no** Modbus exception for a missing or unavailable sensor, but returns sentinel values:
     - `41006 = [239]`, `41011 = [239]` (`0x00EF`, outside `0..100 %`)
     - `41007 = [32767]`, `41013 = [32767]` (`0x7FFF`, outside `250..10000 ppm` or `0..10000 ppm` respectively)
  2. While the setup probe in `detect_slave_details` correctly discards these sentinel values via `_is_plausible` (`_PLAUSIBLE_RANGES`), `_fresh_values` (line 695) only checks the following at runtime:

     ```python
     if value is None or (isinstance(value, float) and not math.isfinite(value)):
     ```

  3. Since `239` and `32767` are of type `int`, they pass this check unhindered! If an external wireless sensor (`M-WRG-II FSF`/`FSC`, see [`models.md`](../reference/models.md) line 83) fails, a sensor briefly returns `0x7FFF`/`0xFFFF`/`239`, or a user selects an `-F`/`-FC`/`O/VOC-AUL` profile in the profile dialog, then `239 %` humidity and `32767 ppm` CO2/VOC are written to Home Assistant as valid measurements (including long-term statistics `SensorStateClass.MEASUREMENT`).
- **(c) Reproducing `pytest` proof based on the real measurement data from [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt):**

```python
async def test_runtime_poll_accepts_hardware_sentinel_values_from_r2_scan(
    client: MeltemModbusClient, unit: MockModbusUnit
) -> None:
    # Exact register values from readable-register-scan-R-2.txt (lines 69..78)
    unit.holding.update({
        41000: [0, 32704, 0, 32704, 2622, 16785],
        41006: [239, 32767],
        41009: [0, 32704],
        41011: [239, 32767, 32767],
    })
    state = await client.read_room_state(_FC_VOC, RoomState(), _TEMPERATURES)

    # BUG: 239% humidity and 32767 ppm CO2/VOC are accepted unfiltered!
    assert state.humidity_extract_air == 239
    assert state.humidity_supply_air == 239
    assert state.co2_extract_air == 32767
    assert state.voc_supply_air == 32767
```

---

### [P2-2] `MeltemRoomDevice` does not restrict `ExtractAirQuality` (`41006..41007`) and `SupplyAirQuality` (`41011..41013`) to profile-supported fields and caches devices only by `room.slave`

- **Severity:** High (block read failure of the entire `temperature` group for unsupported neighbouring registers `41007`/`41012`/`41013`)
- **(a) Exact file and line references:**
  - [`device/device.py`](../../../custom_components/meltem_ventilation/device/device.py): `MeltemRoomDevice.__init__` (lines 45–68)
  - [`device/components.py`](../../../custom_components/meltem_ventilation/device/components.py): `ExtractAirQuality` (lines 127–133, `41006..41007`), `SupplyAirQuality` (lines 136–142, `41011..41013`), `ControlSettings` (lines 153–163, `42000..42005`)
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient._room_device` (lines 217–224), `_due_fields` (lines 611–672)
- **(b) Triggering call and event sequence:**
  1. In `MeltemRoomDevice.__init__`, `_only(self.temperatures, "exhaust_temperature")` is called for `PLAIN_PROFILES`, but neither `ExtractAirQuality` nor `SupplyAirQuality` nor `ControlSettings` is restricted to the capabilities of the profile via `_only(...)`.
  2. As a result, an `-F` device (`ii_f`, `s_f`) reads `41006..41007` (including CO2 `41007`) on every temperature/environment poll, and **every** `-F` and `-FC` device (`ii_f`, `s_f`, `ii_fc`, `s_fc`) reads the 3-register block `41011..41013` on every poll (including the undocumented register `41012` and the unconfirmed VOC register `41013`).
  3. As documented in [`MELTEM.md`](../MELTEM.md) line 111 (`"a block read fails as a whole as soon as one register in it is unreadable"`) and modelled in the project's own fixtures of [`test_config_flow.py`](../../../tests/components/meltem_ventilation/test_config_flow.py) (line 211) and [`test_tools.py`](../../../tests/components/meltem_ventilation/test_tools.py) (lines 138–139: `humidity_unit.fail_read(41007, IllegalDataAddressError()); humidity_unit.fail_read(41013, IllegalDataAddressError())`): as soon as an `-F` or `-FC` device rejects register `41007`, `41012`, or `41013` with `IllegalDataAddressError`, the entire block read for `extract_air_quality` or `supply_air_quality` fails!
  4. Because all 8 temperature and air quality sensors hang off the same read health group `"temperature"` ([`const.py`](../../../custom_components/meltem_ventilation/const.py) lines 242–253), the error on `41013` causes `job.record_error(("temperature",), error)` to be called, and after 3 polls **all** temperature, humidity, and CO2 sensors of the device become `unavailable`!
  5. **Additional error in the device cache:** `MeltemModbusClient._room_device` caches `self._devices` only under `room.slave`. If the same client is first called with a plain profile (`ii_plain`, `exhaust_temperature_only=True`) and then for the same slave with `ii_f`, `self.temperatures` stays permanently trimmed to `"exhaust_temperature"`; `outdoor_air_temperature` and `extract_air_temperature` return `None`, which `_fresh_values` (line 696) treats as `"Invalid outdoor_air_temperature readback: None"`.
- **(c) Reproducing `pytest` proof (based on the `humidity_unit` configuration from [`test_tools.py`](../../../tests/components/meltem_ventilation/test_tools.py) lines 129–140):**

```python
async def test_ii_f_unit_rejecting_co2_and_voc_fails_entire_temperature_group(
    client: MeltemModbusClient, unit: MockModbusUnit
) -> None:
    # Exactly like humidity_unit in test_tools.py:129-140 and co2_unit in test_config_flow.py:206-211
    unit.holding.update({
        41000: _float_words(21.0) + _float_words(10.0) + _float_words(18.0),
        41006: [45],
        41009: _float_words(19.0),
        41011: [50],
    })
    unit.fail_read(41007, IllegalDataAddressError())
    unit.fail_read(41013, IllegalDataAddressError())

    state = await client.read_room_state(_F, RoomState(), _TEMPERATURES)

    # BUG: Although 41006 and 41011 have valid values (45%, 50%), both stay None
    # and the entire "temperature" group is marked as failed!
    assert state.humidity_extract_air is None
    assert state.humidity_supply_air is None
    assert state.read_health_for("temperature").consecutive_failures == 1
```

---

### [P2-3] Protocol inconsistency for unbalanced status codes (`[4, 0, 228]` on slave 4) and `M-WRG-S` scaling

- **Severity:** Medium–High (protocol deviation from [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) and [`modbus.md`](../reference/modbus.md))
- **(a) Exact file and line references:**
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient._read_mode_group` (lines 543–603, esp. lines 564–575), `_scale_airflow_to_raw` (lines 741–743), `_decode_app_encoded_airflow` (lines 777–783), `_decode_preset_mode` (lines 806–822, esp. lines 813–816)
  - Measurement data: [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (line 19: `41100 x3 [4, 0, 228]`), [`modbus.md`](../reference/modbus.md) (lines 122–131)
- **(b) Triggering input data & contradiction with the measurement data:**
  1. In [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (line 19), slave 4 actually reports `41100..41102 = [4, 0, 228]`.
     - In `_decode_preset_mode` (line 814), the code only checks `raw_extract_target > APP_UNBALANCED_PRESET_BASE` (`228 > 200` without an upper bound!) and decodes `preset_mode = "extract_only"`.
     - At the same time, however, `_decode_app_encoded_airflow` (line 783) checks `airflow <= profile_max_airflow(room.profile)` (`(228 - 200) * 10 = 280 <= 100` → `False`) and returns `None`.
     - In `MeltemModbusClient._read_mode_group` (lines 569–574), `extract_target_level` therefore overwrites a previously known `state.extract_target_level` with `None`, because `raw_extract_target is not None` (`228`) is true but its decoding yields `None`!
  2. On `M-WRG-S` profiles (`profile_max_airflow = 97`), `_decode_app_encoded_airflow` (line 783) rejects the app code `210` (`(210 - 200) * 10 = 100 > 97`) entirely as `None`, although according to [`device-parameters.md`](../reference/device-parameters.md) (lines 33–34, 52) the app and the wireless remote control also use level 10 = `100` (or `210`) on the `M-WRG` series.
- **(c) Evidence based on [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (line 19):**
  - Input `41100..41102 = [4, 0, 228]` with `previous_state = RoomState(operation_mode="unbalanced", extract_target_level=50)`:
  - Result from `MeltemModbusClient.read_room_state`: `state.preset_mode == "extract_only"`, `state.target_level == 0`, but `state.extract_target_level` is cleared from `50` to `None`.

---

### [P3-1] `diagnostics.py` performs a live discovery read before reading `transport_diagnostics()` and thereby overwrites exactly the diagnostic counters it is supposed to export

- **Severity:** Medium–High (corruption of diagnostic data & unintended link recycle during the diagnostics download)
- **(a) Exact file and line references:**
  - [`diagnostics.py`](../../../custom_components/meltem_ventilation/diagnostics.py): `async_get_config_entry_diagnostics` (lines 59–106, esp. lines 68–84)
  - [`device/transport.py`](../../../custom_components/meltem_ventilation/device/transport.py): `TransportPolicy.diagnostics` (lines 88–99), `TransportPolicy._link_answered` (lines 149–151), `TransportPolicy._count_timeout` (lines 153–171)
- **(b) Triggering call and event sequence:**
  1. In `async_get_config_entry_diagnostics`, `gateway_units = await coordinator.async_discover_gateway_units()` is called first in line 68, and **only afterwards** `"transport": coordinator.client.transport_diagnostics()` in line 84.
  2. `async_discover_gateway_units()` sends two real Modbus requests to slave 1 (`43901` and `43902..`) via the same `TransportPolicy`.
  3. If the gateway answers (even if room units had timeouts before), `TransportPolicy.run` calls `self._link_answered()` and sets `self._consecutive_timeouts = 0` as well as `self._last_answer_at = time.monotonic()`. As a result, the diagnostics export always shows `0` for `consecutive_timeouts` and always `0.0` for `seconds_since_any_answer`! (Exactly this artefact occurred in the live test on 2026-10-15, see [`HARDWARE_BACKLOG.md`](../HARDWARE_BACKLOG.md) lines 646–649: `seconds_since_any_answer=0` because of the diagnostics download!)
  4. If, on the other hand, the gateway does not answer, the diagnostics download first blocks behind `_gateway_lock`, produces up to 2 timeouts of `0.8 s` each plus the rate slot wait, and can even trigger `await unit.disconnect()` (a disconnect of the shared Modbus link!) via `TransportPolicy._count_timeout` as a side effect of the diagnostics download.
- **(c) Reproducing `pytest` proof:**

```python
async def test_diagnostics_download_wipes_transport_timeout_counters(
    hass: HomeAssistant,
) -> None:
    link = MockModbusConnection()
    link.for_unit(1).holding.update({43901: 1, 43902: [2]})
    client = MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")
    client._policy._consecutive_timeouts = 2
    client._policy._last_answer_at = time.monotonic() - 42.0

    entry = _entry(hass, with_runtime=False)
    coordinator = MeltemDataUpdateCoordinator(
        hass, config_entry=entry, client=client, rooms=[_ROOM], max_requests_per_second=10.0
    )
    entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    # BUG: The diagnostics call overwrote consecutive_timeouts=2 and seconds_since_any_answer=42.0!
    assert diag["coordinator"]["transport"]["consecutive_timeouts"] == 0
    assert diag["coordinator"]["transport"]["seconds_since_any_answer"] == 0.0
```

---

### [P3-2] Unnecessary hold time of `_gateway_lock` in `_async_refresh_room_after_write` and dead third retry branch (`POST_WRITE_REFRESH_RETRIES`)

- **Severity:** Medium–High (all other rooms blocked for 2.5+ s after an already confirmed write + dead code)
- **(a) Exact file and line references:**
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.async_set_preset_mode` (line 736), `MeltemDataUpdateCoordinator.async_activate_intensive` (line 769), `MeltemDataUpdateCoordinator.async_deactivate_intensive` (line 782), `MeltemDataUpdateCoordinator._async_refresh_room_after_write` (lines 952–997)
  - [`const.py`](../../../custom_components/meltem_ventilation/const.py): `POST_WRITE_REFRESH_RETRIES = 2`, `POST_WRITE_REFRESH_INTERVAL_SECONDS = 2.5` (lines 63–64)
- **(b) Triggering call and event sequence:**
  1. For `async_set_preset_mode`, `async_activate_intensive`, and `async_deactivate_intensive`, `refresh_attempts=2` is passed as `min_refresh_attempts=2` to `MeltemDataUpdateCoordinator._async_refresh_room_after_write` while `self._gateway_lock` is held.
  2. Even if the **first** readback (`attempt == 0`, after `1.5 s` `WRITE_SETTLE_SECONDS`) already reads the new state successfully and `self._confirm_pending_writes(updated_states)` sets the write to `"confirmed"`, the loop in line 993 (`if failed or attempt + 1 >= max(1, min_refresh_attempts): return`) does **not** exit, but sleeps for another `2.5 s` (`POST_WRITE_REFRESH_INTERVAL_SECONDS`) with `_gateway_lock` held and polls the gateway a second time!
  3. Conversely, because of `attempt + 1 >= max(1, min_refresh_attempts)`, the loop always exits after 1 attempt (`attempt=0`) with `min_refresh_attempts=1` and always after 2 attempts (`attempt=1`) with `min_refresh_attempts=2` – even if the write is still `"pending"`. The third loop iteration (`attempt=2` from `range(POST_WRITE_REFRESH_RETRIES + 1)`) is unreachable in the entire code base.
- **(c) Evidence from the branch coverage output (`pytest --cov=custom_components.meltem_ventilation --cov-branch`):**

  ```text
  Name                                                   Stmts   Miss Branch BrPart  Cover   Missing
  custom_components\meltem_ventilation\coordinator.py      491      8    154      8    98%   ..., 961->exit, 989->991, 996->961, ...
  ```

  The branches `961->exit` (regular loop end after `POST_WRITE_REFRESH_RETRIES + 1`) and `996->961` are never reached, because no caller ever passes `min_refresh_attempts > 2`.

---

### [P3-3] Options flow `async_step_edit_profiles` performs up to 75 serial Modbus reads just by opening the form, contrary to its documentation

- **Severity:** Medium–High (UI blocked for up to >60 s at a low request rate / contradiction with the code docstring & [`README.md`](../../../README.md))
- **(a) Exact file and line references:**
  - [`config_flow.py`](../../../custom_components/meltem_ventilation/config_flow.py): `MeltemVentilationOptionsFlow.async_step_edit_profiles` (lines 631–666, esp. lines 651–659), `_async_probe_units` (lines 295–314)
  - [`modbus_helpers.py`](../../../custom_components/meltem_ventilation/modbus_helpers.py): `_PROBE_COMPONENTS` (lines 46–52), `detect_slave_details` (lines 260–294)
  - [`README.md`](../../../README.md): lines 190–191 (`"Change profiles for existing units — reassign profiles without rescanning the gateway"`)
- **(b) Triggering call and event sequence:**
  1. When the user opens the item `"Change profiles for existing units"` in the options menu (`MeltemVentilationOptionsFlow.async_step_edit_profiles`, whose docstring reads: `"""Edit the profiles for already known units without rescanning."""`), line 652 nevertheless calls `probed, _ = await _async_probe_units(slaves, coordinator.async_probe_slave_details)`.
  2. The detected profile (`_`) is thrown away; the call only serves to re-read `preview` from the bus, although `existing_rooms[slave].get("preview")` is already stored in `ConfigEntry.data`!
  3. Per device, `detect_slave_details` sends 5 single reads (`40002`, `41006`, `41011`, `41007`, `41013`), all of which run through the `TransportPolicy` of the running coordinator and are therefore subject to the rate limit `max_requests_per_second`.
  4. With 6 devices (30 reads) and `max_requests_per_second = 0.5` (`2.0 s` per read), just opening the form takes **`60 seconds`** (with 15 devices **`150 seconds`**) before Home Assistant even shows the form.

---

### [P3-4] Faulty lifecycle cleanup on a partial setup failure in `async_setup_entry` and missing timer rescheduling in `update_request_rate`

- **Severity:** Medium (background timer leak on setup abort / delayed adoption of a higher request rate)
- **(a) Exact file and line references:**
  - [`__init__.py`](../../../custom_components/meltem_ventilation/__init__.py): `async_setup_entry` (lines 185–234, esp. lines 223–230)
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.update_request_rate` (lines 926–932), `MeltemDataUpdateCoordinator._schedule_next_tick` (lines 465–484)
- **(b) Triggering call and event sequence:**
  1. **Timer leak on setup failure:** In `async_setup_entry` (lines 223–230), `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)` is called. As soon as the first platform (`sensor`) adds its entities, they register listeners on the coordinator, whereupon `DataUpdateCoordinator.async_add_listener` immediately starts the internal refresh timer (`coordinator._unsub_refresh`) on the event loop. If a subsequent platform in `async_forward_entry_setups` raises an exception, the `except Exception:` block does call `client.shutdown()` and `del entry.runtime_data`, **but not `await coordinator.async_shutdown()`**! The refresh timer of the orphaned coordinator stays active.
  2. **No timer rescheduling in `update_request_rate`:** `MeltemDataUpdateCoordinator.update_request_rate` calls `self._schedule_next_tick()`, which merely sets `self.update_interval = timedelta(...)`. The property setter `DataUpdateCoordinator.update_interval` in Home Assistant, however, only changes `self._update_interval_seconds` without calling `self._schedule_refresh()`; an already running longer timer sleep is not shortened.
- **(c) Reproducing `pytest` proof (for the setup abort leak):**

```python
async def test_partial_platform_setup_failure_leaks_coordinator_timer(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    orig_forward = hass.config_entries.async_forward_entry_setups

    async def _fail_after_first_platform(entry, platforms):
        await orig_forward(entry, [Platform.SENSOR])
        raise RuntimeError("second platform failed")

    with (
        patch(f"{_INIT}.resolve_preferred_port_path", side_effect=lambda p: p),
        patch(f"{_INIT}.MeltemModbusClient.async_validate_gateway", new=AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", side_effect=_fail_after_first_platform),
        pytest.raises(RuntimeError, match="second platform failed"),
    ):
        await async_setup_entry(hass, entry)
    # Coordinator refresh timer was started by Platform.SENSOR, but never stopped via async_shutdown() in the except block!
```

---

### [P3-5] Missing reset of `_level_fallbacks` on subsequent regular writes and redundant device registry updates (`25×` per room)

- **Severity:** Low–Medium (stale `last_write_fallback` attributes & unnecessary event loop work)
- **(a) Exact file and line references:**
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.level_write_fallback` (lines 343–353), `MeltemDataUpdateCoordinator._record_level_fallback` (lines 355–357), `MeltemDataUpdateCoordinator.async_set_direction_level` (lines 592–668)
  - [`entity.py`](../../../custom_components/meltem_ventilation/entity.py): `MeltemEntity._async_update_device_registry_versions` (lines 117–131)
- **(b) Triggering call and event sequence:**
  1. **`_level_fallbacks` is not cleared on subsequent writes without a fallback:** If a user sets the supply air to `60` while the opposite direction is unknown (`other is None`), `MeltemDataUpdateCoordinator._record_level_fallback` stores `self._level_fallbacks[room_key] = ("both_directions_balanced_manual", now)` (and fires a second `async_update_listeners()` immediately after the write). If the user sets the extract air to `40` one second later (before the next `flow_control` poll), `MeltemDataUpdateCoordinator.async_set_direction_level` uses the pending overlay `(60, 60)` (so `other = 60` and `operation_mode = "manual"` are known – **no** fallback!). Nevertheless, `self._level_fallbacks[room_key]` stays set, so `last_write_fallback` on both fan entities keeps reporting `"both_directions_balanced_manual"`.
  2. **`25×` `device_registry.async_update_device` per room:** In `MeltemEntity`, **every single entity** holds its own `self._exported_versions = (None, None)`. On the first coordinator update (when `_hw_version` is set) and again on the first `hours` poll (when `_sw_version` is added), each of the up to 25 entities of a room calls `dr.async_get(self.hass).async_update_device(device.id, ...)` for the same `device.id` in `MeltemEntity._async_update_device_registry_versions` (with 6 rooms, up to 150 identical registry calls).

---

### [P4] Test audit for false confidence (mock tautologies & untested paths)

Running `pytest --cov=custom_components.meltem_ventilation --cov-branch --cov-report=term-missing`, `ruff check`, and `mypy` shows:

- **Overall result:** `699 passed, 1 skipped` (`98 %` combined statement/branch coverage; `30` untested statements, `25` untested branch transitions).
- **Key finding on test quality:** The high nominal coverage of `98 %` hides four serious **mock tautologies**, where tests merely check the behaviour of their own test doubles:

| # | Test file & location | Mock tautology | Hidden error in the real code |
| --- | --- | --- | --- |
| 1 | [`test_coordinator_and_config_helpers.py`](../../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) (`_FakeClient.read_room_state`, line 85) & [`test_coordinator_integration.py`](../../../tests/components/meltem_ventilation/test_coordinator_integration.py) (line 85) | `_FakeClient.read_room_state` raises `MeltemModbusError("boom")` for `room.key == "broken"`. | The real `MeltemModbusClient.read_room_state` **never** raises `MeltemModbusError` on `ModbusTimeoutError`, `0x05 AcknowledgeError`, or `GatewayTargetError`. As a result, `_room_failures`, `last_job_error`, and `_on_transport_failure` have no effect on device errors in real operation (**[P1-1]**). |
| 2 | [`test_select_platform.py`](../../../tests/components/meltem_ventilation/test_select_platform.py) (lines 109, 130–134) | Mocks `coordinator.optimistic_preset_mode` with return value `PRESET_MODE_INACTIVE` (`"inactive"`). | The real `MeltemDataUpdateCoordinator.async_clear_preset_mode` calls `_optimistic_presets.clear()` instead of `.set(..., PRESET_MODE_INACTIVE)` and never sets an `"inactive"` overlay; moreover, there is no overlay at all for `operation_mode`. |
| 3 | [`test_diagnostics_and_system_health.py`](../../../tests/components/meltem_ventilation/test_diagnostics_and_system_health.py) (`_coordinator()`, line 42) | Uses a free `MagicMock()` with a static `coordinator.last_job_error = None` and a decoupled `transport_diagnostics` mock. | The real property `MeltemDataUpdateCoordinator.last_job_error` (line 233) has **0 % coverage**, and the side effect that `async_discover_gateway_units()` in [`diagnostics.py`](../../../custom_components/meltem_ventilation/diagnostics.py) overwrites the real `transport_diagnostics()` counters before they are read (**[P3-1]**) goes undetected. |
| 4 | [`test_config_flow.py`](../../../tests/components/meltem_ventilation/test_config_flow.py) (line 648) & [`test_coordinator_and_config_helpers.py`](../../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) (lines 322, 553) | Mocks `coordinator.update_request_rate` in the options flow as `MagicMock()`; in `test_fallback_is_cleared_by_the_next_successful_readback` sets `coordinator.data` directly instead of via `_confirm_pending_writes`. | Line 932 (`self._schedule_next_tick()` in `MeltemDataUpdateCoordinator.update_request_rate`) and lines 1125–1126 (`del self._level_fallbacks[room_key]` in `MeltemDataUpdateCoordinator._confirm_pending_writes`) were not executed in a single test of the entire suite (`Miss` in the coverage report). |

---

### [P5] Home Assistant Quality Scale (IQS), schema, and documentation consistency

1. **Discrepancy between [`triggers.yaml`](../../../custom_components/meltem_ventilation/triggers.yaml) / [`conditions.yaml`](../../../custom_components/meltem_ventilation/conditions.yaml) and the automation documentation ([`TRIGGER_FILTER_CHANGE_DUE.md`](../../TRIGGER_FILTER_CHANGE_DUE.md), [`CONDITION_FILTER_CHANGE_DUE.md`](../../CONDITION_FILTER_CHANGE_DUE.md)):**
   - **(a) References:** [`triggers.yaml`](../../../custom_components/meltem_ventilation/triggers.yaml) (lines 1–5), [`conditions.yaml`](../../../custom_components/meltem_ventilation/conditions.yaml) (lines 1–5), [`TRIGGER_FILTER_CHANGE_DUE.md`](../../TRIGGER_FILTER_CHANGE_DUE.md) (lines 17, 37–40), [`CONDITION_FILTER_CHANGE_DUE.md`](../../CONDITION_FILTER_CHANGE_DUE.md) (lines 18, 36–39).
   - **(b)/(c) Finding:** In [`triggers.yaml`](../../../custom_components/meltem_ventilation/triggers.yaml) and [`conditions.yaml`](../../../custom_components/meltem_ventilation/conditions.yaml), only `device: - integration: meltem_ventilation` is defined under `target:`; an `entity:` block (`domain: binary_sensor`, `device_class: problem`, `integration: meltem_ventilation`) is missing. In the Home Assistant UI, the target selector therefore only allows selecting a *device* (including the gateway device `Meltem Gateway M-WRG-GW`, which has no `filter_change_due` entity at all!), while [`TRIGGER_FILTER_CHANGE_DUE.md`](../../TRIGGER_FILTER_CHANGE_DUE.md) (line 17) and [`CONDITION_FILTER_CHANGE_DUE.md`](../../CONDITION_FILTER_CHANGE_DUE.md) (line 18) instruct the user: *"4. Select a Meltem device or its **Filter change due** entity as the target"*.
2. **Outdated statements in [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md):**
   - **(a) References:** [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md) (lines 65, 92–94, 129–134, 151–153) vs. [`test_platform_matrix.py`](../../../tests/components/meltem_ventilation/test_platform_matrix.py) (line 215).
   - **(b)/(c) Finding:**
     - [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md) claims in three places (lines 92, 130, 152) that `test_device_path_sensor_keeps_its_registry_entry_across_reloads` must be excluded with `--deselect` in the coverage run, because its teardown tries to open `/dev/ttyACM0`. In fact, `setup_profile_fixture` in [`test_platform_matrix.py`](../../../tests/components/meltem_ventilation/test_platform_matrix.py) (lines 101–116) mocks `async_validate_gateway` and `async_refresh`, so all `700` tests (`699 passed, 1 skipped`) run without errors and without `--deselect`.
     - In addition, [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md) claims in line 138 that [`config_flow.py`](../../../custom_components/meltem_ventilation/config_flow.py) has `100 %` coverage; with branch coverage (`--cov-branch`), [`config_flow.py`](../../../custom_components/meltem_ventilation/config_flow.py) is at `99 %` (branch `271->279` in `_temporary_units`: a repeated lookup of the same cached `slave` in `unit_for` is not exercised in any test, because each unit ID is queried only once in the setup flow).
3. **Contradiction between [`CHANGELOG.md`](../../../CHANGELOG.md) and the actual behaviour of `write_control_setting`:**
   - **(a) References:** [`CHANGELOG.md`](../../../CHANGELOG.md) (line 283) vs. [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py) (lines 176–180, 785–804) and [`test_coordinator_and_config_helpers.py`](../../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) (lines 916–971).
   - **(b)/(c) Finding:** [`CHANGELOG.md`](../../../CHANGELOG.md) (line 283) documents under 3.0.0: *"Humidity and CO2 settings are published optimistically after a successful write and stay visible if the immediate confirmation read fails."* In the current code (4.2.0), however, there is no `OptimisticOverlay` for `control_setting:*` in `self._overlays`, and `test_control_setting_keeps_confirmed_value_when_refresh_fails` tests exactly the opposite (namely that the old value `50` instead of the written value `70` stays visible when the readback fails), without this behaviour change being noted in the changelog.
4. **Schema consistency (`strings.json`, `translations/de.json`, `translations/en.json`, `icons.json`):**
   - The structure and keys of [`strings.json`](../../../custom_components/meltem_ventilation/strings.json), [`en.json`](../../../custom_components/meltem_ventilation/translations/en.json), [`de.json`](../../../custom_components/meltem_ventilation/translations/de.json), and [`icons.json`](../../../custom_components/meltem_ventilation/icons.json) are fully in sync and cover all entity, exception, System Health, and flow keys.

---

## 2. Summary of the recommended fixes (roadmap)

1. **[P1-1] Repair error propagation between `MeltemModbusClient` and `MeltemDataUpdateCoordinator`:**
   - In `MeltemDataUpdateCoordinator._read_one_job`, evaluate whether all groups due in `job.refresh_plan` failed in this pass (`last_attempt >= read_started_at` and `last_error is not None`). If so, increment `_room_failures[room.key]` and set `_last_job_error` (analogous to [`benchmark_integration_like.py`](../../../tools/benchmark_integration_like.py) lines 227–238).
   - Do not count answers to the gateway-local register `40101` (`REGISTER_RF_COMM_STATUS`) as a sign of life of the end device for `seconds_since_read_answer(unit_id)` when all device registers (`41xxx`/`42xxx`) fail.
2. **[P1-2] Add timestamp protection to `OptimisticOverlay`:**
   - Store the `started_at` time (`dt_util.utcnow()`) in `OptimisticOverlay`, and in `get()` and `settle()` only compare against `confirmed` when `read_health.last_successful_read >= pending.started_at` holds.
3. **[P1-3 & P1-4] Consolidate `balanced_target_readback` and `target_levels`:**
   - Use `state.balanced_target_readback` in `target_levels` with priority over the coarse grid-of-10 status value `state.target_level`, and keep it across subsequent polls as long as `state.operation_mode` and `state.target_level` (`41101`) do not change.
   - In `_decode_balanced_target_readback`, do not mix `state.target_level` with `derive_balanced_airflow(...)` (measured values), and in `target_levels` (lines 72–77) return `(state.supply_air_flow, state.extract_air_flow)` instead of `(common, common)` when `state.target_level is None` and `airflow_is_fresh`.
4. **[P2-1 & P2-2] Runtime plausibility check & profile-exact component restriction:**
   - Apply `_is_plausible` (`_PLAUSIBLE_RANGES`) in `_fresh_values` as well, so that hardware sentinels (`239`, `32767`, `65535`) are discarded.
   - In `MeltemRoomDevice.__init__`, also apply `_only(...)` to `self.extract_air_quality`, `self.supply_air_quality`, and `self.control_settings` according to `room.profile`, and cache `self._devices` by `(room.slave, room.profile)`.
5. **[P3-1 to P3-5] Clean up diagnostics, post-write refresh, options flow & lifecycle:**
   - In `async_get_config_entry_diagnostics`, read `coordinator.client.transport_diagnostics()` **before** `await coordinator.async_discover_gateway_units()`.
   - In `MeltemDataUpdateCoordinator._async_refresh_room_after_write`, return immediately as soon as the write is no longer `"pending"` (`not self._writes.needs_confirmation(room.key, write_key)`).
   - In `MeltemVentilationOptionsFlow.async_step_edit_profiles`, skip the repeated Modbus probing when `existing_rooms[slave].get("preview")` is already present.
   - In `async_setup_entry`, add `await coordinator.async_shutdown()` to the error path of `async_forward_entry_setups`, and reschedule the active refresh timer in `update_request_rate`.
   - Clear `_level_fallbacks` on a successful write without a fallback, and run `_async_update_device_registry_versions` per room instead of per entity.
