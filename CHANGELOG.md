# Changelog

## 2.0.0

- Reworked quick-mode handling to better match real device behavior.
  - `Intensive ventilation` is now a separate action button instead of a normal quick-mode entry.
  - Added `Intensive ventilation active` as a dedicated status entity.
  - Improved quick-mode readback, fallback handling, and post-write refreshes.
- Simplified and cleaned up the airflow UI.
  - Removed redundant derived airflow sensors.
  - Kept the shared airflow slider as the main control for the common balanced case.
  - Improved labels, icons, and quick-mode naming for better day-to-day usability.
- Improved stability with real hardware.
  - Short transient communication failures now keep the last valid state instead of immediately flipping everything to unavailable.
  - Added more defensive handling when optional mode registers are only partially readable.
- Improved device information.
  - Firmware and hardware information are now shown more cleanly on the device card.
  - Removed the separate software-version sensor after moving that information into device info.
- Cleaned up diagnostics and terminology.
  - Removed undocumented diagnostic flags that were not meaningfully interpretable.
  - Clarified user-facing names for modes, airflow values, and error state.
- Improved setup and options UX.
  - The profile-editing flow now shows the configured room/device name so existing units are easier to identify.
- Expanded reverse-engineering notes, helper tools, and automated test coverage.

## 1.1.0

- Some minor resource optimizations

## 1.0.0 - Initial version

- Initial release of the Meltem Home Assistant integration
- USB and gateway-backed discovery for Meltem `M-WRG-GW`
- Support for `M-WRG-S` and `M-WRG-II` profile variants
- Sensors, binary sensors, airflow controls, operation mode selection, and supported humidity/CO2 settings
- Config flow, options flow, diagnostics, and system health support
- Polling and write behavior tuned against real gateway hardware
