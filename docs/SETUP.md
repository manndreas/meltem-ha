# Setup and configuration

For installation and the initial setup steps, see the [README](../README.md).

## Identifying units

During setup, the profile list shows each unit's App setup code when available.
Match it to the code below the DataMatrix on the unit label to identify which
Modbus address belongs to each physical unit. Choose the profile using the
model information on the type plate.

For known product IDs, the setup preview also shows a series and product
identifier from the app catalog. The older-series mapping has not yet been
verified on hardware. Existing units with a known ID show the identifier in
their device details.

Units are listed by their Modbus address and initially named `Unit 1`, `Unit 2`
and so on. Each unit appears as its own entry under the gateway on the
integration page. Rename them like any other Home Assistant device; the new
name is also shown when you change a profile later.

## Changing the gateway connection

Choose `Reconfigure` in the integration entry menu to change the gateway
connection or switch between USB serial and RTU over TCP. The new connection
is checked before it is saved; existing units and entity IDs remain.

Enter the network bridge hostname or IP address and TCP port manually.
Hostnames, including `.local` names, must be resolvable by the Home Assistant
host. Automatic network discovery is not supported. See the
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
