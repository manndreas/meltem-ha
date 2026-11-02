# Removing the integration

The integration uses Home Assistant's standard removal flow. Remove the
Home Assistant config entry before uninstalling the integration files.

## Remove the Home Assistant config entry

1. Go to **Settings** > **Devices & services** > **Integrations**.
2. Open **Meltem Modbus**.
3. Open the integration's menu and select **Delete**.
4. Confirm the removal.

Home Assistant unloads the integration, its entities, and its connection to
the gateway. You do not need to remove the entities individually.

## Remove a single unit

Each unit is listed as its own entry under the gateway on the **Meltem
Modbus** integration page. Open the menu of that unit and select **Delete**.
Home Assistant removes the unit's device and entities and reloads the
integration; the other units keep running. If the gateway still reports the
unit, Home Assistant shows a repair notice, and **Add unit** or **Scan for new
units** adds it again.

## Uninstall the integration

If you installed the integration through the Marketplace, open **Settings** >
**Marketplace** > **Installed**, select **Meltem Modbus**, and uninstall it.
Restart Home Assistant afterwards.

If you installed it manually, remove the
`custom_components/meltem_ventilation/` directory from your Home Assistant
configuration directory, then restart Home Assistant.

Removing the integration only affects Home Assistant. It does not reset or
change the gateway, ventilation units, or their settings in the Meltem app.
