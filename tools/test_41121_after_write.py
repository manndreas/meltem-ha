#!/usr/bin/env python3
"""Test whether register 41121 becomes readable after a write on each unit."""

from __future__ import annotations

import asyncio

import tools.benchmark_integration_like as t


async def read_u16(connection, slave: int, address: int) -> int | None:
    registers = await t.read_raw(connection, slave, address, 1)
    return None if registers is None else registers[0]


async def main() -> int:
    port = "/dev/ttyACM0"
    connection = t.open_connection(port)

    try:
        rooms = await t.discover_rooms(connection, port)
        client = t.MeltemModbusClient(connection.for_unit, port=port)

        print(f"rooms: {[room.slave for room in rooms]}")
        for index, room in enumerate(rooms, start=1):
            print()
            print(f"room_index={index} slave={room.slave}")

            baseline_state = await client.read_room_state(
                room,
                t.RoomState(),
                t.RefreshPlan.only(refresh_airflow=True),
            )
            baseline_flow = baseline_state.target_level or baseline_state.supply_air_flow
            if baseline_flow is None:
                print("  baseline airflow unavailable, skipping")
                continue

            before = await read_u16(connection, room.slave, 41121)
            print(f"  before write 41121={before} baseline_flow={baseline_flow}")

            target = 10 if baseline_flow != 10 else 20
            await client.write_level(room, target)
            print(f"  wrote target={target}")

            immediate = await read_u16(connection, room.slave, 41121)
            print(f"  immediate 41121={immediate}")

            await asyncio.sleep(5.0)
            after_5s = await read_u16(connection, room.slave, 41121)
            flow_after_5s = await t.read_raw(connection, room.slave, 41020, 2)
            print(f"  after 5s 41121={after_5s} flow={flow_after_5s}")

            await client.write_level(room, int(baseline_flow))
            print(f"  restored target={baseline_flow}")
            await asyncio.sleep(3.0)
            restored = await read_u16(connection, room.slave, 41121)
            flow_restored = await t.read_raw(connection, room.slave, 41020, 2)
            print(f"  restored 41121={restored} flow={flow_restored}")

        return 0
    except (t.ModbusConnectionError, t.MeltemConnectionError) as err:
        print(f"ERROR: lost the serial connection on {port}: {err}")
        return 2
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
