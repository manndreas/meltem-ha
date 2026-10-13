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

## Uninstall the integration

If you installed the integration with HACS, open **HACS** > **Integrations**,
select **Meltem Modbus**, and use its menu to remove it. Restart Home Assistant
afterwards.

If you installed it manually, remove the
`custom_components/meltem_ventilation/` directory from your Home Assistant
configuration directory, then restart Home Assistant.

Removing the integration only affects Home Assistant. It does not reset or
change the gateway, ventilation units, or their settings in the Meltem app.
