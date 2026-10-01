# M-WRG-GW gateway

Manufacturer facts about the `M-WRG-GW` gateway (Art.-Nr. `200383`). Sources:
`GW` (installation and operating manual, 2 pages), `APP`, and the accessory
tables of the unit manuals.

Most of this integration's gateway knowledge comes from hardware observation,
not from these documents: USB serial Modbus, the bridge address
`device_id=1`, and discovery via `43901` / `43902..`. Those observations are
in [../DEVELOPER.md](../DEVELOPER.md).

## Purpose

- Makes cloud-based operation of `M-WRG-II` and `M-WRG` units from build year
  2020 onward possible from an iOS or Android app (`GW` 1.2).
- Sits between the units and the Meltem app. Units can be controlled,
  parameterised and read out locally or worldwide online. Depending on the
  unit configuration, time programs can be stored, and device functions and
  sensors can be parameterised by the user (`GW` 1.2).
- The user creates a personal account in the app to configure and manage the
  gateway. The app also sets up and controls the radio link between gateway
  and units. Data is stated to be fully encrypted and shared only with the
  user's consent (`GW` 1.2).
- Can be used in parallel with the radio remote control `M-WRG-FBH`, the
  4-way radio push button `M-WRG-FT` and the radio sensors `M-WRG-II FSF`
  (humidity) and `M-WRG-II FSC` (CO2). It works with the wall-integrated `U2`
  installation variant and all covers of both series (`GW` 1.2).

## Capacity and radio

| Item | Value | Source |
| --- | --- | --- |
| Units per gateway | up to 15 | `GW` 1.2 |
| Radio range | up to 70 m in free field; depends on the building | `GW` 1.2 |
| Radio frequency | 868.3 MHz, Rx class 2, at least 0 dBm output power | `GW` 1.5 |
| Installation constraint | never install inside a metal enclosure, otherwise no radio link to the units | `GW` 4 |

Prerequisites for app control (`GW` 1.2):

- units of the `M-WRG-II` or `M-WRG` series, build year 2020 or later
- smartphone or tablet with iOS or Android and the Meltem app
- gateway with Meltem cable and power supply
- router with internet access (provided by the customer)

## Technical data

| Item | Value |
| --- | --- |
| Dimensions (W × H × D) | 105 × 80 × 25 mm |
| Weight | approx. 90 g |
| Housing | ABS, white similar to RAL 9010 |
| Operating temperature | 0 °C to 50 °C |
| Storage / transport temperature | -25 °C to 60 °C |
| Relative humidity | 5 % to 90 %, non-condensing |
| Power supply | plug-in power supply 230 V AC to 5 V DC with micro-USB-B plug, max. 500 mA |
| Power input | micro-USB-B socket, 5 V DC, max. 200 mA current consumption |
| Network | Ethernet 10/100 Mbit, RJ-45 socket |
| Use | dry indoor rooms only |

Source: `GW` 1.5, 2.

The manual documents the micro-USB-B socket **only as the power input**. It
does not mention a USB data or serial interface. The USB Modbus access that
this integration relies on is therefore not covered by any collected document.

## Package contents

Gateway, plug-in power supply with micro-USB-B cable, Ethernet cable
(`GW` 3).

## Controls and connectors

| Position | Element |
| --- | --- |
| 1 | Radio connection LED (Funkverbindungs-LED) |
| 2 | Network LED |
| 3 | Power LED |
| 4 | Reset button (on the back, needs a pointed object) |
| 5 | Power connector (micro-USB-B) |
| 6 | Ethernet connector (RJ-45) |

Three keyhole mounts on the back allow horizontal or vertical wall mounting
(`GW` 4.1).

## Commissioning

1. Connect the Ethernet port to the local network, for example a router LAN
   port.
2. Plug the power supply into the micro-USB-B socket and a 230 V outlet. The
   power LED lights up, and after a few seconds the green and orange LEDs at
   the Ethernet socket light up.
3. Install the Meltem app, create or log in to an account, and follow the app
   to register the gateway and connect units.

Source: `GW` 4.2, 4.3. App-side details are in [app.md](app.md).

## LED states

### Front LEDs

| LED | State | Cause | Remedy |
| --- | --- | --- | --- |
| Power | off | no power supply | connect the power supply |
| Power | blinking | gateway does not start | reset to factory settings |
| Network | off | no network (cloud) connection | run first installation in the app; check Ethernet LEDs; check internet access |
| Radio | off | no radio connection, or no unit registered | run first installation in the app |
| Radio | blinking | no radio connection to a registered unit | check that the unit is switched on; reconnect gateway and unit via the app |

### RJ-45 LEDs

| LED | State | Cause | Remedy |
| --- | --- | --- | --- |
| Green | off | no Ethernet link | check the Ethernet cable |
| Orange | off | no DHCP server, no IP address available | enable DHCP in the network or set a fixed IP in the app |
| Orange | blinking | no DHCP server; the default IP `192.168.0.207` is used | enable DHCP in the network or set a fixed IP in the app |

Source: `GW` 5.1, 5.2.

## Factory reset

- Press the reset button for at least 10 s.
- All LEDs go out and then blink. The power LED then lights and the gateway
  restarts. This takes about 30 s.
- All radio connections and login information are removed from the gateway.

Source: `GW` 6.

## Relevance for this integration

- The documented operating model is cloud-based app control. This matches the
  observation in [../DEVELOPER.md](../DEVELOPER.md) that no useful local LAN
  control interface was found.
- Gateway and units talk over 868.3 MHz radio. Every Modbus request to a unit
  behind the gateway is therefore relayed over radio. That is consistent with
  the slow, sequential gateway behaviour and with the timing questions in
  [../HARDWARE_BACKLOG.md](../HARDWARE_BACKLOG.md) (HW-2).
- Up to 15 units per gateway is the documented upper bound for the unit count
  read from `43901`.
- Without DHCP the gateway falls back to `192.168.0.207`. This helps when
  probing the LAN side.
- A factory reset removes all radio pairings. After a reset, units must be
  paired again through the app before they reappear in `43902..`. This is an
  inference from the documented reset behaviour.

## Open questions and contradictions

- The unit manuals list the gateway manual as Dok.-Nr. `2500001|DE`, while the
  gateway manual itself is numbered `2400001|DE|2025-01-01`.
- The build-year limit (2020+) for app or gateway use differs from the remote
  control, which supports units from build year 2018 (`FBH` 1.2).
- The USB serial interface and its Modbus bridge behaviour are undocumented.
