# Setup and configuration

For installation and the initial setup steps, see the [README](../README.md).

## Identifying units

During setup, the profile list shows each unit's App setup code when available.
Match it to the code below the DataMatrix on the unit label to identify which
Modbus address belongs to each physical unit. Choose the profile using the
model information on the type plate; the
[README](../README.md#supported-profiles) shows which type plate belongs to
which profile.

For known product IDs, the setup list also shows the device family from the
app catalog. The older-family mapping has not yet been verified on hardware.
If the selected profile belongs to a different family, a warning lets you
correct the selection or explicitly continue. Unknown or unreadable product
IDs do not cause a warning. Existing units with a known ID show the product
identifier in their device details.

Units are listed by their Modbus address. New devices are initially named
after the selected model family and, when available, their setup code, for
example `Meltem M-WRG-II (R000001)`. Home Assistant shows this editable name
when assigning devices to areas. If a code cannot be read, the name contains
only the model family; duplicate initial names receive a numeric suffix.

Each unit appears as its own entry under the gateway on the integration page.
Rename devices like any other Home Assistant device; the new name is also
shown when you change a profile later. Existing device names are not changed.

## Changing the gateway connection

Choose `Reconfigure` in the integration entry menu to change the gateway
connection or switch between USB serial, RTU over TCP, and Modbus TCP/MBAP.
The new connection is checked before it is saved; existing units and entity
IDs remain.

Enter the network endpoint or bridge hostname/IP address and TCP port manually.
Hostnames, including `.local` names, must be resolvable by the Home Assistant
host. Raw RTU-over-TCP uses port `5020` by default; Modbus TCP/MBAP uses `502`.
For MBAP, enter the gateway's address if its TCP endpoint is available, or
the address of a compatible bridge. On a separate MBAP-to-RTU bridge, set its
response timeout to about 1 second; see
[Diagnostics](DIAGNOSTICS.md#modbus-tcp-through-a-separate-bridge) for
why. Automatic network discovery is not supported. See the
[installation requirements](../README.md#requirements) for connection and
security limitations.

## Integration options

Open the options via `Settings` -> `Devices & Services` -> `Meltem Modbus` ->
`Configure`.

- **Change request rate** adjusts the shared read-request cap. It defaults to
  **4 requests per second** and can be set from **0.5 to 10**. See
  [polling and diagnostics](./DIAGNOSTICS.md) for details.
- **Scan for new units** reads the gateway's unit list again: new units are
  added and moved units are updated, and the profiles of all units can be
  changed in the same step. Units the gateway no longer reports are listed
  for confirmation before they are removed together with their devices.
