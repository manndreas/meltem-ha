#!/usr/bin/env python3
"""Test whether register 41121 becomes readable after a write on each unit."""

from __future__ import annotations

import time

import tools.benchmark_integration_like as t


def main() -> int:
    settings = t.SerialSettings(
        port="/dev/ttyACM0",
        baudrate=t.FIXED_BAUDRATE,
        bytesize=t.FIXED_BYTESIZE,
        parity=t.FIXED_PARITY,
        stopbits=t.FIXED_STOPBITS,
        timeout=float(t.FIXED_TIMEOUT),
    )
    rooms = t.discover_rooms(settings)
    client = t.MeltemModbusClient(settings)

    try:
        print(f"rooms: {[room.slave for room in rooms]}")
        for index, room in enumerate(rooms, start=1):
            print()
            print(f"room_index={index} slave={room.slave}")

            baseline_state = client.read_room_state(
                room,
                t.RoomState(),
                t.RefreshPlan.only(refresh_airflow=True),
            )
            baseline_flow = baseline_state.current_level or baseline_state.supply_air_flow
            if baseline_flow is None:
                print("  baseline airflow unavailable, skipping")
                continue

            modbus = client._ensure_client()
            before = client._read_optional_uint16(modbus, room.slave, 41121)
            print(f"  before write 41121={before} baseline_flow={baseline_flow}")

            target = 10 if baseline_flow != 10 else 20
            client.write_level(room, target)
            print(f"  wrote target={target}")

            modbus = client._ensure_client()
            immediate = client._read_optional_uint16(modbus, room.slave, 41121)
            print(f"  immediate 41121={immediate}")

            time.sleep(5.0)
            modbus = client._ensure_client()
            after_5s = client._read_optional_uint16(modbus, room.slave, 41121)
            flow_after_5s = client._read_optional_uint16_block(modbus, room.slave, 41020, 2)
            print(f"  after 5s 41121={after_5s} flow={flow_after_5s}")

            client.write_level(room, int(baseline_flow))
            print(f"  restored target={baseline_flow}")
            time.sleep(3.0)
            modbus = client._ensure_client()
            restored = client._read_optional_uint16(modbus, room.slave, 41121)
            flow_restored = client._read_optional_uint16_block(modbus, room.slave, 41020, 2)
            print(f"  restored 41121={restored} flow={flow_restored}")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
