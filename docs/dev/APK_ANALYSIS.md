# Meltem Android app 1.17: static analysis

This is a technical reference derived from the supplied Android app, not an
official protocol specification. It concentrates on the app's own behaviour
and data rather than attempting to document every bundled third-party library.
The app and decompiled source are not distributed with this repository.

## Evidence and limits

- Input: `Meltem_1.17_APKPure.xapk`, SHA-256
  `aab569135d6d1988e449026b138db12444788d8a053d44b8bbfada56c7141f1d`.
- Package `com.meltem.cloudapp`, version name `1.17`, version code `117`.
  The base APK SHA-256 is
  `d2695dff93e6eb93f128569af777b5a6345137f4c6f00ecc53c25aaf27b19532`.
- The XAPK came from a third-party distributor. Its origin and equivalence to
  an official store download were **not** independently verified. All five
  APKs report the same signing-certificate fingerprint and v1/v2/v3 signature
  presence; this is not an independent verification of the publisher or a
  cryptographic verification of every signature.
- Static inspection only: the app was not installed or run, no cloud endpoint
  was contacted, and no gateway or device was read or written for this
  analysis. Existing live observations are cited separately.
- The archive contains the base APK and splits for ARM64, mdpi, English and
  Spanish. The base has two DEX files and an approximately 2.1 MB plain
  React Native JavaScript bundle with 852 Metro modules. The bundle refers
  to a source map that is not present in the base APK.
- The JavaScript bundle was formatted and its application-specific modules
  inspected. The first-party `com.meltem` DEX classes were decompiled with
  Androguard 4.1.4; some control-flow decompilation reported errors, so Java
  output is not necessarily a faithful or compilable reconstruction. ARM64
  native libraries and all third-party dependencies were inventoried, not
  exhaustively disassembled or security-audited.
- References such as **JS 696** mean the numeric Metro module in
  `assets/index.android.bundle`, not a source file or stable identifier
  across versions. Native references name the DEX class. Tables describe
  metadata present in this particular app, including inconsistencies; they
  do not prove which paths or values work on a particular device.

See also [APP_CATALOG.md](APP_CATALOG.md) for the existing product and sensor
analysis, [MELTEM.md](MELTEM.md) for actual gateway measurements,
[SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md) for local setting experiments,
and [reference/app.md](reference/app.md) for the manufacturer's app manual.
Manufacturer facts and static app findings must not be conflated.

After this static analysis, a separate hardware session checked selected
[register leads](MELTEM.md#app-derived-register-survey-on-2026-11-03), the
[time-program status](MELTEM.md#app-time-program-comparison-on-2026-11-03),
and [Modbus TCP access](MELTEM.md#read-only-modbus-tcp-comparison-on-2026-11-03).
Those observations and their limits are recorded there, not in this static
inventory.

## Executive summary

1. Normal unit control is cloud-mediated: the phone sends HTTPS requests to
   the cloud, which handles the gateway and radio-linked units. The app also
   performs a separate, local Modbus TCP gateway setup. A gateway's WebSocket
   URL is configured locally; the inspected app code does not establish that
   the phone itself opens this WebSocket. See **JS 683, 805, 812, 813**.
2. The product catalog has two entries, `0001c874` and `0001c853`, with shared
   register metadata but differing selectable settings and service tags.
   The cloud product ID is not proven to be interchangeable with local
   `40002` on every device. See **JS 696** and
   [APP_CATALOG.md](APP_CATALOG.md#product-entries).
3. Persistent settings use a tag selector and a separate write/status
   protocol involving cloud registers `40911`, `40901`, `40949` and `40954`.
   An app/cloud register is not automatically a locally accessible Modbus
   holding register. See **JS 786**.
4. The app's schedule editor uses **Monday = 0, Sunday = 6**, including the
   values placed in its `dayOfWeek` API field. A controlled save of a future
   Wednesday switch point was consistent with `49012=2` locally, but the
   cloud payload and actual scheduled execution were not captured. See
   **JS 686, 787, 789** and the hardware comparison in [MELTEM.md](MELTEM.md#app-time-program-comparison-on-2026-11-03).
5. Service metadata exposes calibration, frost protection, flap timing,
   factory reset and other sensitive settings. Presence in the bundle does
   not justify writing them. The service menu is conditional on
   `Settings.SERVICE`; that property is not defined in the inspected default
   settings object. See **JS 696, 697, 782, 786**.

## Packaging, platform and data handling

### Android application

The decoded manifest declares minimum SDK 21, target SDK 35 and compile SDK
33; the native build configuration says `release` and `DEBUG = false`.
`com.meltem.MainActivity` is the exported launcher activity. The manifest
sets `allowBackup=false`; the declared Google API activity is not exported.
It declares camera features optional and requests:

| Permission | Apparent purpose or limitation |
| --- | --- |
| `INTERNET` | Cloud API and local network access. |
| `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | Network/Wi-Fi state. |
| `CHANGE_WIFI_MULTICAST_STATE` | Network discovery support. |
| `CAMERA` | QR scanning in setup. |
| `SYSTEM_ALERT_WINDOW` | Declared; a necessary use was not established here. |

The native split includes React Native/JNI, JavaScriptCore, Hermes-related
libraries, networking and image libraries. Their presence alone does not
establish which JavaScript engine is active. The app includes React Navigation,
Redux, AsyncStorage, camera and UDP integrations among other dependencies.
German and English app text is in **JS 686**; Android resource splits also
contain English and Spanish resources. A resource split is not by itself proof
that the app's complete interface is translated into that language.

### Local storage and credentials

**JS 692** stores the session ID, username, schedule templates and cache, and
remembered supply/extract selections through React Native AsyncStorage.
Logout clears the listed values except saved schedule templates; the session
and username are also held in process globals. No app-layer encryption is
visible in this storage wrapper. This does not establish the protections
provided by a particular Android device.

The bundle contains a shared application API key. Its value is intentionally
not reproduced here; it must not be treated as a private per-user credential.
The session ID, passwords, gateway QR/setup code, identifiers and personal data
must never be put into documentation, logs or examples. Several HTTPS GET
requests put session information in the query string, so sanitized logging
and capture handling are important. HTTPS and WSS are configured, but this
inspection is not a complete TLS or certificate-pinning audit.

## Network architecture and cloud API

**JS 697** specifies the HTTPS API base
`https://api.connect2myhome.eu/v1` and gateway endpoint
`wss://gw.connect2myhome.eu/meltem`. **JS 683** appends the paths below.
GET arguments are URL-encoded query parameters; normal POSTs are
`application/x-www-form-urlencoded`. The time-program set request instead
places common identifiers in the query and sends JSON in the POST body.
Most authenticated requests include `apiKey` and `sessionId`; login includes
`apiKey`, `username` and `password`. Error `10201` invokes logout, and
error `401` invokes logout plus an invalid-key alert.

These are observed client calls, not a promise of API stability. A successful
HTTP request is not the same as a device-side operation or a confirmed
physical state.

| Method and path under `/v1` | Purpose and additional request fields |
| --- | --- |
| `POST /user/auth` | Log in with username and password. |
| `GET /user/details` | Account details, including username. |
| `POST /user/logout`, `POST /user/delete` | End session or delete account. |
| `POST /user/register` | Username, password, email, language and validation flag. |
| `POST /user/updatePassword`, `POST /user/updateEmail` | Change credentials or email; includes current password. |
| `POST /user/updateLanguage` | Set account language. |
| `POST /user/resetPassword`, `POST /user/recoverUsername` | Start account recovery. |
| `POST /bridge/register` | Register a gateway using its MAC address. |
| `GET /bridge/list`, `GET /bridge/devices`, `GET /bridge/products` | List gateways, devices or bindable products. |
| `POST /bridge/info`, `GET /bridge/info/status` | Initiate and poll gateway information. |
| `POST /bridge/unbind` | Unbind a gateway. |
| `POST /bridge/bindProduct` | Bind product; optional serial number and `skipP2P` path. |
| `GET /bridge/bindProduct/status`, `POST /bridge/bindProduct/abort` | Check or abort binding by `statusId`. |
| `POST /bridge/zone/create`, `GET /bridge/zone/list` | Create or list zones. |
| `POST /bridge/zone/update`, `POST /bridge/zone/delete` | Update or remove zone. |
| `POST /bridge/updateName`, `POST /bridge/setTimeZone` | Gateway name and time-zone settings. |
| `GET /device/data/live` | Cached/live values for `deviceId` and `registers`. |
| `POST /device/data/get`, `GET /device/data/get/status` | Start an explicit register read and poll its `statusId`. |
| `POST /device/data/set`, `GET /device/data/set/status` | Start a register write with `register` and `values`; a set-status endpoint is defined. |
| `GET /device/unbind`, `GET /device/unbind/status` | Unbind unit and check status. |
| `POST /device/zone/set`, `POST /device/updateName` | Assign zone or change unit name. |
| `GET /device/timeprogram/get` | Get switch points. |
| `POST /device/timeprogram/set`, `GET /device/timeprogram/set/status` | Replace schedule and poll completion. |

**JS 682** expects status `5000` for a completed data read, `2000` for a
completed data write, and `6000` for a completed schedule write. A pending
read can report `105`; `5030` causes one retry in one settings-read path, and
`6030` terminates the schedule-write wait as failure. Despite the separately
defined `data/set/status` route, the inspected `readWriteStatus` helper calls
the **data/get/status** wrapper and checks `2000`. This is a property of the
client code, not proof that all server versions require that route.

Common retries use up to eight iterations, initially 25 ms and doubling the
sleep; individual paths differ in termination and error handling. The
single-unit overview requests live values every 3 s. Schedule write polling
uses a 100 ms interval and a 120 s timeout; failed schedule writes may be
retried for up to three rounds. These cloud/UI timings are not appropriate
defaults for this integration's sensitive local bus.

## Gateway discovery and setup on the local network

**JS 805, 812, 813** and native classes
`com.meltem.bridgeSetupModule.BridgeSetupModule` and `TcpClient` show the
following setup path:

1. The app listens for UDP traffic on port `35353`. The JavaScript message
   handler uses the sender IP, deduplicates addresses and starts a local
   discovery request. The separately present native `UDPClient` filters for
   text containing `modbus`, but that is not the same as the inspected
   JavaScript listener, which does not check the message contents.
2. The Android native client connects to that IP on TCP port `502`. It sends
   Modbus TCP with unit ID `1`, function `0x03`, first for raw start address
   `0xA034` / decimal `41012` and count `3` for the MAC, then `0xA04A` /
   `41034` and count `57` for the 114-byte server URL. These are **gateway**
   addresses, not room-unit sensor registers with the same numbers.
3. An empty URL is classified as ready for setup. A URL beginning with the
   configured WSS endpoint and containing a bridge ID is compared against
   the account's bridge list to classify it as this account's or another
   account's setup.
4. Setup registers the MAC with the cloud and uses the returned bridge ID.
   The QR/manual gateway code is decoded and checked locally; it is not a
   cloud password. The app constructs a function `0x10` write starting at
   raw address `0xA046` / `41030`, containing decoded credential bytes, a
   zero-padded 114-byte URL and a zero-padded 16-byte GUID field. The URL
   points to the WSS endpoint with `bridgeId` as a query parameter.
5. After the write response, the app polls the cloud for gateway availability.
   On success it requests daylight-saving type `4` and time-zone offset
   `60` before the naming screen. This is what the client sends, not proof of
   the server's interpretation or a recommendation for other time zones.

The inspected native TCP path uses ordinary TCP, not TLS; setup credentials
therefore require a trusted local network. The code reads a response with
individual socket reads; this static analysis does not prove correct handling
of fragmented TCP responses. A second native `ModbusProtocol` implementation
contains similar primitives, but the inspected React Native path constructs
the frames in JavaScript. No gateway setup writes should be tried as part of
normal integration discovery.

The expression in **JS 805** assigns `"ios"` to `Platform.OS` before invoking
`tcpPing`, rather than comparing it. The Android native `tcpPing` method shown
by decompilation only logs. The actual impact of that apparent assignment
requires runtime verification; it is not evidence that an Android broadcast
ping is sent.

## Product catalog and displayed values

| Cloud catalog ID | Decimal | Series label | Product identifier | Notable app differences |
| --- | ---: | --- | --- | --- |
| `0001c874` | 116852 | M-WRG-II | `VMD-22RPS44` | Five keypad settings; two-sensor settings; 149 listed service tags. |
| `0001c853` | 116819 | M-WRG | `VMD-22RES22` | Three keypad settings; no two-sensor settings list; 104 listed service tags. |

The bundled catalog has no separate `M-WRG-II P` / `M-WRG-II E` product
entries or an app-side exchanger selector in the examined product lookup.
The tested P and E units also share the same local product ID and product
name; see [APP_CATALOG.md](APP_CATALOG.md#product-entries). The APK therefore
provides no evidence that exchanger type can be auto-detected from its product
catalog or app-specific sensor metadata. Continue to rely on the type plate;
do not infer P/E from `41106`, `41113`, or an unverified service tag.

Both catalog entries use the same active, setting, error and online-status
register lists. The app groups names using `~` as a location/area/name
delimiter. Multi-unit actions are serialized within each bridge group and
may run across different bridge groups concurrently (**JS 684**).

### Register metadata

The following is the complete register-key inventory in **JS 696**, grouped
where neighbouring entries share a purpose. `R`, `W` and `RW` are the app's
labels, not independently established Modbus access rights.

| Key | App purpose | Access / caveat |
| --- | --- | --- |
| `40101` | RF communication problem | R; used for online-status checks. |
| `40103` | Unit fault | R; included in error checks. |
| `41000`, `41002`, `41004` | Exhaust, inlet and outlet temperatures | R; metadata range `-200..200`, UI uses strict bounds. Local measured temperature mapping differs; see [MELTEM.md](MELTEM.md#temperature-register-quirk). |
| `41006`, `41011` | Exhaust and supply humidity | R; metadata min 1, max 100. |
| `41007`, `41012` | Exhaust and supply CO2 | R; metadata min 0, max 32766. |
| `41008`, `41013` | Exhaust and supply VOC | R; app says ppm, but the physical unit/scaling is not verified. |
| `41009` | Supply temperature | R; same temperature metadata. |
| `41015` | Ventilation-speed capability bits | R. |
| `41016`, `41017` | Fault code, dirty-filter flag | R; included in error checks. |
| `41019` | Actual speed mode | R; marked for product `0001c853`. |
| `41020`, `41021` | Exhaust and inlet fan speed | R; app labels percent. |
| `41027`, `41030` | Filter time remaining and operating hours | R; days and hours respectively. |
| `41100..41102` | Current mode and submodes | R; **key `41100` erroneously contains `register: 41101`** in metadata. |
| `41103..41105` | Temporary override mode and submodes | R. |
| `41106` | Scheduler state | R. |
| `41113`, `41133` | Legacy speed/mode read and write | R and RW respectively; see the control path below. |
| `41120..41125` | Set mode, submodes and temporary override | W. |
| `42000..42005` | Humidity and CO2 thresholds and minimum/maximum levels | RW; the six are requested as settings. |
| `42006..42009` | Priority override mode, level, delay and run-on time | RW metadata only; not in the normal six-register settings list. |
| `49000` | Time-program operation | RW metadata; action values are listed below. |

The normal overview's ordered active list is `41000, 41002, 41004, 41006,
41007, 41009, 41011, 41013, 41020, 41021, 41027, 41030, 41113, 41100,
41101, 41102, 41103, 41104, 41106`; `40103, 41016, 41017` are appended
as error values. `41008`, `41012`, `41015`, `41019` and `41105` are in
metadata but not that normal active list.

The sensor validity check requires an address and numeric value, then uses
strict `>` / `<` comparisons for nonzero min/max metadata. Therefore humidity
metadata `1..100` accepts `2..99`, while a zero lower bound is not enforced.
Invalid exhaust humidity `239` and invalid exhaust CO2 `32767` explicitly
disable the corresponding controls; other missing or invalid readings are not
the same as these sentinels. See **JS 783** and
[APP_CATALOG.md](APP_CATALOG.md#sensor-capability-detection).

The UI derives absolute humidity from temperature and relative humidity rather
than reading an extra register. The filter display changes colour at 10 days.
The app's VOC unit labels and temperature labels must not override local
measurements.

## Runtime modes and airflow

**JS 696, 761, 783** contain two app control encodings. In the modern path
the cloud write starts at `41120` with a 13-value array, so it includes
`41132` as its final element. This describes the cloud request shape, not
necessarily one local Modbus transaction.

| Selection | First values in the app's 13-word array |
| --- | --- |
| LOW / MED / HIGH | `[3, 228/229/230, 0, ...]` |
| Automatic / CO2 / humidity | `[2, 16/144/112, 0, ...]` |
| Standby | `[1, 0, 0, ...]` |
| Intensive override | `[0, 0, 0, 3, 227, 0, ...]` |
| Night / interval / temperature | `[3, 226, ...]` / `[3, 231, ...]` / `[2, 224, ...]` |
| Unbalanced | `[4, supplyCode, extractCode, 0, ...]` |

For the unbalanced UI path, a positive slider value is encoded as
`round(value / 10) + 200`; zero remains zero. The controls use 10-unit UI
steps. The metadata also lists continuous submodes `201..210`, automatic
submodes `16, 80, 48`, and CO2 submodes `144, 208, 176`; that does not
establish every variant's meaning.

The older control path writes `41133`: `(round(speed / 10) << 12) |
(modeCode << 8) | 169`. The UI actually calls it for certain balanced/manual
and older cross-air selections; it is not merely dead metadata. Its paired
read decoding uses `41113`. The existence of this cloud path is **not**
evidence that writing local `41133` is supported or safe.

Schedule activation/deactivation uses a 13-element `41120` write with element
at offset 6 set to `1` / `3`. The overview interprets scheduler-state values
`1`, `2`, or `4` as active. Do not infer the meanings of all other values.
The separate local integration's verified write sequences and safety
constraints remain in [MELTEM.md](MELTEM.md#operating-mode-and-set-registers).

## Settings and parameter tags

### Direct settings

The app requests settings in this order: `42001`, `42002`, `42003`, `42004`,
`42005`, `42000`. Its metadata gives `42000` humidity threshold 40..80,
default 60; `42003` CO2 threshold 400..1400, step 10, default 600; and the
four min/max levels a UI step of 10 and defaults 10/60. Units and limits in
the app are not automatically equivalent to the local register contract; see
[reference/modbus.md](reference/modbus.md) and
[reference/device-parameters.md](reference/device-parameters.md).

For direct-setting writes the app starts a cloud write and then polls live
data for an equal value. Other paths use asynchronous job status. Neither
mechanism establishes physical behaviour.

### Tag read and write protocol

For a selected tag, **JS 786**:

1. writes the tag number to cloud register `40911`, waits for the write-status
   helper, requests `40901`, and polls that read's status for the value;
2. writes a changed tag to `40949` as the comma-separated four-value sequence
   `[tag, 0, rawValue, 0]`;
3. after the write-status helper, requests `40954` and accepts a single
   returned value of `2` as the tag-write status. It then displays the value
   it sent; this particular check is **not** a fresh read of `40901`.

The metadata's `index` is a separate catalog/display field, not the tag
number or a demonstrated local register. For a `stepTag`, the UI sends
`round(displayValue / stepTag)`; on read it multiplies the raw value by that
scale, with signed-16-bit interpretation where the minimum is negative.
Concurrency can race the shared tag selector, and no local availability of
these addresses follows from the cloud protocol. The previously attempted
local checks are described in [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).

### User-facing tag metadata

This table inventories all 33 entries in the shared non-service tag table.
The app does not show all entries in every screen or on every product.
Ranges/defaults are app metadata, not measured safe bounds. `—` means no
explicit step in that entry; the settings renderer normally falls back to 1.

| Tag | Purpose | Unit | Range | UI step / raw scale | Default |
| ---: | --- | --- | --- | --- | ---: |
| 7 | Standby pause | min | 5..255 | — | 60 |
| 8 | Standby sample time | min | 1..255 | — | 5 |
| 9 | Standby sample airflow | m³/h | 10..100 | — | 20 |
| 10 | Close flaps in standby | bool | 0..1 | — | 1 |
| 11 | Intensive airflow | m³/h | 0..100 | — | 100 |
| 12 | Intensive duration | min | 0..240 | — | 15 |
| 13 / 14 / 15 | Keypad LOW / MED / HIGH | m³/h | 0..100 | — | 10 / 30 / 60 |
| 19 | Five-button switch enabled | bool | 0..2 | — | 2 |
| 33 | Available internal sensors | raw | 0..255 | — | 8 |
| 42 / 43 | Supply-excess: extract / supply | m³/h | 0..100 | — | 0 / 50 |
| 44 / 45 | Key 4 extract / supply; second label says key 5 | m³/h | 0..100 | — | 70 / 70 |
| 46 / 47 | Extract-excess: extract / supply | m³/h | 0..100 | — | 50 / 0 |
| 58 | Night ventilation duration | min | 0..1440 | 10 | 360 |
| 60 / 61 | Night ventilation supply / extract | m³/h | 0..100 | — | 10 / 10 |
| 65 | Temperature program airflow | m³/h | 0..100 | — | 30 |
| 66 / 67 | Interval operating / pause airflow | m³/h | 0..100 | — | 30 / 0 |
| 68 / 69 | Interval operating / pause time | min | 1..1440 | 10 | 60 / 60 |
| 76 | Filter buzzer enabled | bool | 0..1 | — | 1 |
| 81 / 82 | Temperature program minimum / maximum supply temperature | °C | 0..50 | 0.5 / 0.5 | 3 / 30 |
| 93 | Restore factory settings | bool | 0..1 | — | 0 |
| 151 | Two-sensor control enabled | bool | 0..1 | — | 1 |
| 152 | VOC control threshold | app says ppm | 0..4000 | 10 | 1000 |
| 153 | Minimum supply airflow for two-sensor control | m³/h | 0..100 | — | 10 |
| 154 | Two-sensor control delay | min | 0..120 | — | 10 |

For ordinary settings, the app includes `11, 12, 13, 14, 15, 42, 43, 46,
47, 7, 8, 9, 10, 76` for both products; M-WRG-II adds `44, 45, 151..154`.
Interval uses `67, 66, 69, 68`; temperature uses `65, 81, 82`; the night
list is `58, 60, 61`. Tag presence does not prove a feature is visible,
configured, or supported by every firmware.

Important inconsistencies:

- The service table gives tag `7` a minimum of 1 and tag `8` a minimum of 5,
  opposite to the ordinary table above. The manufacturer's remote-control
  reference agrees with the service-table order, not the ordinary table.
- The ordinary HIGH default is 60; the shared service entry says 50.
- The ordinary two-sensor threshold `152` defaults to 1000 with a step of 10;
  the service entry says 1500 with a step of 1.
- Tag `154` is labelled a **delay in minutes** in both app tables, not a
  maximum airflow. Tag `76` is a **buzzer** in both tables, not a cross-air
  enable switch. These are app-label findings, not new hardware measurements.
- The extracted `46` / `47` labels are extract / supply. Any differing
  interpretation in another implementation requires isolated verification.
- The second label for `45` says key 5, while the related manual describes
  supply for key 4. Treat the label as inconsistent, not a new mapping.

## Service catalog inventory

**JS 696** contains 155 shared service metadata entries plus per-product
overrides for tags `58..61`. The M-WRG-II service list selects 149 tags and
the M-WRG list selects 104. A table entry existing does not mean it is
selected for both series. In particular shared tags `35` and `197` are not
in either product's service list. These parameters may control safety,
calibration or factory state: **do not write them based on this catalog**.

The following inventory covers every shared tag ID, grouped by the app's
labels. It is an index, not a substitute for the per-entry metadata in the
bundle or the manufacturer's parameter reference.

| Tag IDs | App-described family |
| --- | --- |
| `1..6`, `160`, `161`, `197` | Frost protection: balancing, temperature thresholds, minimum supply, maximum extract, timing and flap-related option. |
| `7..10`, `86` | Standby pause, sample, airflow, flap and pre-sample settings. |
| `11..15`, `57` | Intensive airflow/time, LOW/MED/HIGH and continuous level. |
| `16..19`, `119`, `120` | Five-button switch modes and enable setting. |
| `20..23` | Fan-speed deviation and retry-time metadata. |
| `24..32`, `76` | Filter lifetime, high-speed duration, buzzer timing/intervals and enable. |
| `33..35`, `195` | Available sensors, boot mode, RF disable and humidity-related minimum-speed option. |
| `36..41`, `50`, `70..75`, `77..80`, `91`, `172` | Humidity/CO2 thresholds, min/max levels, automatic selection, PID factors, sampling/filtering, hysteresis and minimum difference. |
| `42..49`, `87..90`, `196` | Supply/extract cross-air levels, including medium/high variants and temporary duration. |
| `51..53` | BCD choice settings. |
| `54..56`, `95` | Priority/external-input level, delay, run-on and mode. |
| `64..69` | Binding time; temperature-program level; interval levels and times. |
| `81..85` | Temperature-program bounds, unit/fan hours and hours-save interval. |
| `92`, `123`, `131` | Unix time, time-zone offset and daylight saving. |
| `93`, `97`, `60110` | Restore factory settings, save factory settings and store NVM. |
| `96` | External off-button enable. |
| `98`, `99` | Day ventilation supply/extract levels. |
| `101`, `168..170` | External three-position switch mode and position modes. |
| `102..104` | Min/max airflow limits and maximum-flow RPM offset. |
| `122`, `132` | Mode and status LED on-time. |
| `124`, `125` | Presence-detector level and run-on time. |
| `126..130` | Day/night minimum-speed timing, levels and enable. |
| `133`, `134` | Smoke-detection mode and contact type. |
| `151..155`, `171` | Two-sensor enable, threshold, minimum supply, delay, ramp-down and minimum difference. |
| `162..167` | Supply/extract flap timings and open/close duty cycles. The app gives `164` and `165` the same open-time label; do not infer the latter's intended meaning. |
| `173`, `174` | Fan control P/I factors. |
| `175..184` | Supply-fan RPM offsets for nominal flow points 10 through 100 m³/h. |
| `185..194` | Extract-fan RPM offsets for nominal flow points 10 through 100 m³/h. |

Selected service metadata illustrates why the raw scale matters:

| Tags | App range | UI step / `stepTag` | App default |
| --- | --- | --- | --- |
| `2`, `3` | -20..20 °C | 0.01 / 0.01 | -1.5 / 4 |
| `4`, `5` | 0..100 / 10..100 % | 1 / 0.1 | 0 / 60 |
| `70..75`, `173`, `174` | 0..50 | 0.001 / 0.001 | Depends on tag. |
| `81`, `82` | 0..50 °C | 0.5 / 0.5 | 3 / 30 |
| `123` | -720..840 min | 1 / 1 | 60 |
| `162..165` | 0..6000 s | 0.1 / 0.1 | 0.7 |
| `175..194` | -1000..1000 RPM | 1 / 1 | Depends on flow point. |

The per-product service overrides disagree for night ventilation. M-WRG-II
describes `58` as runtime, `60` as supply and `61` as extract, defaults
360 min / 10% / 30%. M-WRG describes `58` as start time, `59` as duration,
and `60` / `61` as supply / extract, defaults 1320 min / 480 min / 50% / 0%.
The ordinary tag table has yet another `61` default of 10. These are distinct
metadata contexts, not established device defaults.

### Complete shared service metadata

The table below records every entry in the shared service object. `Index` is
the app's catalog index, **not** a register address. `Step` is the UI step;
`Scale` is `stepTag`, the multiplier used to translate a raw value to a
displayed value. The range and default are the app's display metadata.
`II` and `S` indicate that the corresponding product's service list contains
the tag; `—` means neither list selects it. This is not a hardware capability
guarantee. Tags `83` and `84` are marked read-only; the other access
behaviours are not established by this table.

| Tag | Index | Range | Step | Scale | Default | Unit | Listed for |
| ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| 1 | 1 | 0..1 | 1 | 1 | 0 | — | II, S |
| 2 | 2 | -20..20 | 0.01 | 0.01 | -1.5 | °C | II, S |
| 3 | 3 | -20..20 | 0.01 | 0.01 | 4 | °C | II, S |
| 4 | 4 | 0..100 | 1 | 0.1 | 0 | % | II, S |
| 5 | 5 | 10..100 | 1 | 0.1 | 60 | % | II, S |
| 6 | 6 | 10..600 | 1 | 1 | 180 | sec | II, S |
| 7 | 9 | 1..255 | 1 | 1 | 60 | min | II, S |
| 8 | 11 | 1..255 | 1 | 1 | 5 | min | II, S |
| 9 | 12 | 10..100 | 1 | 1 | 20 | % | II, S |
| 10 | 13 | 0..1 | 1 | 1 | 1 | — | II, S |
| 11 | 14 | 0..100 | 1 | 1 | 100 | % | II, S |
| 12 | 15 | 0..240 | 1 | 1 | 15 | min | II, S |
| 13 | 16 | 0..100 | 1 | 1 | 10 | % | II, S |
| 14 | 17 | 0..100 | 1 | 1 | 30 | % | II, S |
| 15 | 18 | 0..100 | 1 | 1 | 50 | % | II, S |
| 16 | 21 | 0..18 | 1 | 1 | 0 | — | II, S |
| 17 | 22 | 0..18 | 1 | 1 | 1 | — | II, S |
| 18 | 23 | 0..18 | 1 | 1 | 2 | — | II, S |
| 19 | 26 | 0..3 | 1 | 1 | 2 | — | II, S |
| 20 | 0 | 0..100 | 1 | 1 | 50 | % | S |
| 21 | 0 | 1..30 | 1 | 1 | 30 | min | S |
| 22 | 0 | 0..100 | 1 | 1 | 50 | % | S |
| 23 | 0 | 1..30 | 1 | 1 | 30 | min | S |
| 24 | 33 | 2160..8760 | 1 | 1 | 8760 | hours | II, S |
| 25 | 34 | 0..60 | 1 | 1 | 5 | min | II, S |
| 26 | 35 | 1..5 | 1 | 1 | 1 | sec | II, S |
| 27 | 36 | 1..20 | 1 | 1 | 14 | sec | II, S |
| 28 | 37 | 1..24 | 1 | 1 | 12 | hours | II, S |
| 29 | 38 | 1..24 | 1 | 1 | 9 | — | II, S |
| 30 | 39 | 1..24 | 1 | 1 | 6 | — | II, S |
| 31 | 40 | 1..24 | 1 | 1 | 3 | — | II, S |
| 32 | 41 | 1..24 | 1 | 1 | 1 | — | II, S |
| 33 | 43 | 0..65535 | 1 | 1 | 8 | — | II, S |
| 34 | 0 | 0..1 | 1 | 1 | 0 | — | S |
| 35 | 44 | 0..1 | 1 | 1 | 0 | — | — |
| 36 | 45 | 40..80 | 1 | 1 | 60 | % | II, S |
| 37 | 46 | 0..100 | 1 | 1 | 10 | % | II, S |
| 38 | 47 | 1..100 | 1 | 1 | 60 | % | II, S |
| 39 | 50 | 400..1400 | 1 | 1 | 600 | ppm | II, S |
| 40 | 51 | 0..100 | 1 | 1 | 10 | % | II, S |
| 41 | 52 | 1..100 | 1 | 1 | 60 | % | II, S |
| 42 | 53 | 0..100 | 1 | 1 | 10 | % | II, S |
| 43 | 54 | 0..100 | 1 | 1 | 50 | % | II, S |
| 44 | 57 | 0..100 | 1 | 1 | 70 | % | II, S |
| 45 | 58 | 0..100 | 1 | 1 | 70 | % | II, S |
| 46 | 59 | 0..100 | 1 | 1 | 50 | % | II, S |
| 47 | 60 | 0..100 | 1 | 1 | 0 | % | II, S |
| 48 | 63 | 0..100 | 1 | 1 | 100 | % | II, S |
| 49 | 64 | 0..100 | 1 | 1 | 0 | % | II, S |
| 50 | 66 | 0..1 | 1 | 1 | 0 | — | II, S |
| 51 | 68 | 0..3 | 1 | 1 | 0 | — | II, S |
| 52 | 69 | 0..3 | 1 | 1 | 0 | — | II, S |
| 53 | 70 | 0..3 | 1 | 1 | 0 | — | II, S |
| 54 | 20 | 0..100 | 1 | 1 | 60 | % | II, S |
| 55 | 72 | 0..240 | 1 | 1 | 1 | min | II, S |
| 56 | 73 | 0..240 | 1 | 1 | 15 | min | II, S |
| 57 | 19 | 0..100 | 1 | 1 | 30 | % | II, S |
| 64 | 102 | 1..30 | 1 | 1 | 5 | min | II, S |
| 65 | 91 | 0..100 | 1 | 1 | 30 | % | II, S |
| 66 | 77 | 0..100 | 1 | 1 | 30 | % | II, S |
| 67 | 78 | 0..100 | 1 | 1 | 0 | % | II, S |
| 68 | 79 | 1..1440 | 1 | 1 | 60 | min | II, S |
| 69 | 80 | 1..1440 | 1 | 1 | 60 | min | II, S |
| 70 | 81 | 0..50 | 0.001 | 0.001 | 0 | — | II, S |
| 71 | 82 | 0..50 | 0.001 | 0.001 | 0.03 | — | II, S |
| 72 | 83 | 0..50 | 0.001 | 0.001 | 0 | — | II, S |
| 73 | 86 | 0..50 | 0.001 | 0.001 | 10 | — | II, S |
| 74 | 87 | 0..50 | 0.001 | 0.001 | 10 | — | II, S |
| 75 | 88 | 0..50 | 0.001 | 0.001 | 1 | — | II, S |
| 76 | 42 | 0..1 | 1 | 1 | 1 | — | II, S |
| 77 | 89 | 1..1800 | 1 | 1 | 1 | sec | II, S |
| 78 | 90 | 0..200 | 1 | 1 | 2 | — | II, S |
| 79 | 84 | 1..1800 | 1 | 1 | 1 | sec | II, S |
| 80 | 85 | 0..200 | 1 | 1 | 2 | — | II, S |
| 81 | 92 | 0..50 | 0.5 | 0.5 | 3 | °C | II, S |
| 82 | 93 | 0..50 | 0.5 | 0.5 | 30 | °C | II, S |
| 83 | 94 | 0..0 | 1 | 1 | 0 | hours | II, S |
| 84 | 95 | 0..0 | 1 | 1 | 0 | hours | II, S |
| 85 | 96 | 4..168 | 1 | 1 | 4 | hours | II, S |
| 86 | 10 | 1..255 | 1 | 1 | 2 | min | II, S |
| 87 | 55 | 0..100 | 1 | 1 | 0 | % | II, S |
| 88 | 56 | 0..100 | 1 | 1 | 70 | % | II, S |
| 89 | 61 | 0..100 | 1 | 1 | 70 | % | II, S |
| 90 | 62 | 0..100 | 1 | 1 | 0 | % | II, S |
| 91 | 48 | 0..10 | 1 | 1 | 2 | % | II, S |
| 92 | 97 | 0..2147483647 | 1 | 1 | 0 | sec | II, S |
| 93 | 100 | 0..1 | 1 | 1 | 0 | — | II, S |
| 95 | 71 | 0..18 | 1 | 1 | 12 | — | II, S |
| 96 | 67 | 0..1 | 1 | 1 | 1 | — | II, S |
| 97 | 101 | 0..1 | 1 | 1 | 0 | — | II, S |
| 98 | 0 | 0..100 | 1 | 1 | 30 | % | S |
| 99 | 0 | 0..100 | 1 | 1 | 30 | % | S |
| 101 | 29 | 0..2 | 1 | 1 | 2 | — | II, S |
| 102 | 103 | 0..150 | 1 | 1 | 0 | m³/h | II |
| 103 | 104 | 0..150 | 1 | 1 | 100 | m³/h | II |
| 104 | 105 | -500..500 | 1 | 1 | 0 | RPM | II |
| 119 | 24 | 0..18 | 1 | 1 | 8 | — | II |
| 120 | 25 | 0..18 | 1 | 1 | 14 | — | II |
| 122 | 27 | 0..180 | 1 | 1 | 5 | sec | II |
| 123 | 98 | -720..840 | 1 | 1 | 60 | min | II, S |
| 124 | 106 | 0..100 | 1 | 1 | 60 | % | II |
| 125 | 107 | 0..240 | 1 | 1 | 10 | min | II |
| 126 | 108 | 0..23 | 1 | 1 | 8 | hr | II, S |
| 127 | 109 | 0..23 | 1 | 1 | 20 | hr | II, S |
| 128 | 110 | 0..100 | 1 | 1 | 40 | % | II, S |
| 129 | 111 | 0..100 | 1 | 1 | 20 | % | II, S |
| 130 | 112 | 0..1 | 1 | 1 | 1 | — | II, S |
| 131 | 99 | 0..1 | 1 | 1 | 1 | — | II, S |
| 132 | 28 | 0..180 | 1 | 1 | 5 | sec | II |
| 133 | 113 | 0..18 | 1 | 1 | 18 | — | II |
| 134 | 114 | 0..1 | 1 | 1 | 0 | — | II |
| 151 | 115 | 0..1 | 1 | 1 | 1 | — | II |
| 152 | 116 | 0..4000 | 1 | 1 | 1500 | ppm | II |
| 153 | 117 | 0..100 | 1 | 1 | 10 | % | II |
| 154 | 118 | 0..120 | 1 | 1 | 10 | min | II |
| 155 | 119 | 0..100 | 1 | 1 | 1 | % | II |
| 160 | 7 | -20..20 | 0.01 | 0.01 | 2 | °C | II |
| 161 | 8 | 10..1000 | 1 | 1 | 120 | min | II |
| 162 | 121 | 0..6000 | 0.1 | 0.1 | 0.7 | sec | II |
| 163 | 122 | 0..6000 | 0.1 | 0.1 | 0.7 | sec | II |
| 164 | 123 | 0..6000 | 0.1 | 0.1 | 0.7 | sec | II |
| 165 | 124 | 0..6000 | 0.1 | 0.1 | 0.7 | sec | II |
| 166 | 125 | 0..100 | 0.1 | 0.1 | 100 | % | II |
| 167 | 126 | 0..100 | 0.1 | 0.1 | 100 | % | II |
| 168 | 30 | 0..18 | 1 | 1 | 0 | — | II |
| 169 | 31 | 0..18 | 1 | 1 | 1 | — | II |
| 170 | 32 | 0..18 | 1 | 1 | 2 | — | II |
| 171 | 120 | 0..500 | 1 | 1 | 50 | ppm | II |
| 172 | 49 | 0..20 | 1 | 1 | 2 | % | II |
| 173 | 127 | 0..50 | 0.001 | 0.001 | 1.5 | — | II |
| 174 | 128 | 0..50 | 0.001 | 0.001 | 25 | — | II |
| 175 | 129 | -1000..1000 | 1 | 1 | -20 | RPM | II |
| 176 | 130 | -1000..1000 | 1 | 1 | 20 | RPM | II |
| 177 | 131 | -1000..1000 | 1 | 1 | 50 | RPM | II |
| 178 | 132 | -1000..1000 | 1 | 1 | 90 | RPM | II |
| 179 | 133 | -1000..1000 | 1 | 1 | 115 | RPM | II |
| 180 | 134 | -1000..1000 | 1 | 1 | 115 | RPM | II |
| 181 | 135 | -1000..1000 | 1 | 1 | 150 | RPM | II |
| 182 | 136 | -1000..1000 | 1 | 1 | 160 | RPM | II |
| 183 | 137 | -1000..1000 | 1 | 1 | 190 | RPM | II |
| 184 | 138 | -1000..1000 | 1 | 1 | 200 | RPM | II |
| 185 | 139 | -1000..1000 | 1 | 1 | 60 | RPM | II |
| 186 | 140 | -1000..1000 | 1 | 1 | 60 | RPM | II |
| 187 | 141 | -1000..1000 | 1 | 1 | 105 | RPM | II |
| 188 | 142 | -1000..1000 | 1 | 1 | 110 | RPM | II |
| 189 | 143 | -1000..1000 | 1 | 1 | 180 | RPM | II |
| 190 | 144 | -1000..1000 | 1 | 1 | 210 | RPM | II |
| 191 | 145 | -1000..1000 | 1 | 1 | 160 | RPM | II |
| 192 | 146 | -1000..1000 | 1 | 1 | 265 | RPM | II |
| 193 | 147 | -1000..1000 | 1 | 1 | 280 | RPM | II |
| 194 | 148 | -1000..1000 | 1 | 1 | 330 | RPM | II |
| 195 | 149 | 0..1 | 1 | 1 | 0 | — | II, S |
| 196 | 65 | 0..1440 | 1 | 1 | 180 | min | II |
| 197 | 9 | 0..1 | 1 | 1 | 0 | — | — |
| 60110 | 150 | 0..1 | 1 | 1 | 0 | — | II |

The series-specific service overrides are not part of that shared table:

| Series | Tag | Index | Range | Step / scale | Default | Unit |
| --- | ---: | ---: | --- | --- | ---: | --- |
| M-WRG-II | 58 | 74 | 0..1440 | 1 / 1 | 360 | min |
| M-WRG-II | 60 | 75 | 0..100 | 1 / 1 | 10 | % |
| M-WRG-II | 61 | 76 | 0..100 | 1 / 1 | 30 | % |
| M-WRG | 58 | 0 | 0..1440 | 1 / 1 | 1320 | min |
| M-WRG | 59 | 0 | 0..1440 | 1 / 1 | 480 | min |
| M-WRG | 60 | 0 | 0..100 | 1 / 1 | 50 | % |
| M-WRG | 61 | 0 | 0..100 | 1 / 1 | 0 | % |

## Weekly schedules and templates

**JS 686, 787, 788, 789, 799** show:

- A switch point has `dayOfWeek`, `hour`, `minute`, and `registers` containing
  `49015` mode, `49016` submode and `49017` second submode. The editor creates
  an ID from day/hour/minute and sorts by those fields.
- Day numbers are **0 Monday, 1 Tuesday, 2 Wednesday, 3 Thursday, 4 Friday,
  5 Saturday, 6 Sunday**. The editor converts JavaScript's Sunday-zero
  `Date.getDay()` using `(day + 6) % 7`, labels those values Monday-first,
  and passes the chosen number directly as `dayOfWeek` when building the
  JSON sent by `device/timeprogram/set`. No conversion is visible in that
  request-building path.
- The app checks a maximum of 42 switch points per device and avoids duplicate
  day/time entries. The manufacturer manual states up to six per day and 42
  per unit; this analysis does not claim the client separately enforces a
  six-per-day limit.
- The normal editor gets the existing schedule, modifies a cached copy, sorts
  it and sends the **whole** resulting list to the set endpoint. It polls
  status and reloads. Templates and a per-device cache live on the phone;
  templates are intentionally retained by the app's logout cleanup.
- A second register-oriented helper sets `49011`, writes an action to
  `49000` (`READ=0`, `EDIT=1`, `DELETE=2`, `DELETEALL=3`, `ADD=4`) and
  reads `49001, 49002, 49011..49017`. An RF workaround checks for missing
  points. The existence of this helper does not establish that it is the
  normal JSON API path or a safe local write sequence.
- App schedule airflow editing can encode a positive level as
  `round(value / 10) + 200`; mode `4` uses separate sides. This encoding and
  the local integration's raw airflow scaling are different contexts.

The static app path and the controlled `49012=2` read for a Wednesday point
are consistent with Monday-zero for this app and device. Three additional
rounds across all six units found `41106=0` on units 2–4 and `3` on units 5–7;
the user reported that the latter programs were not active. The values and the
unit-5 schedule-register responses are recorded in
[MELTEM.md](MELTEM.md#cross-unit-scheduler-state-on-2026-11-03). The sample
supports, but does not prove, that `3` means a stored, inactive program.
Neither it nor the one-point save proves how every firmware executes a
weekday. A controlled future multi-point test remains useful.

A subsequent app-only two-point test on unit `4` changed the second point
from LOW to MEDIUM, but the readable `49011..49017` tuple continued to show
the first HIGH point. The user removed both test entries afterwards. This
shows the sampled registers are not a complete, directly readable schedule
list in the tested state; the details are recorded in
[MELTEM.md](MELTEM.md#two-point-app-schedule-on-2026-11-03).

## Implications and open questions

- Do not add local reads or writes for `409xx`, service tags, `41133`,
  `42006..42009`, or gateway setup registers simply because they occur in
  the app. Cloud register names, gateway addresses, tag numbers and
  room-unit holding registers are different namespaces.
- The app's 13-word cloud control request and local serial writes have
  different transport and confirmation semantics. Existing component
  register ranges and request timing remain unchanged.
- Validate the weekday mapping against a future point's actual execution
  before implementing local schedule writes.
- Do not map app service tags `76`, `154`, `46`, or `47` to local Modbus writes:
  the APK metadata alone does not establish a local register mapping or safe
  write sequence.
- App metadata for product, sensors, ranges, defaults, VOC units and service
  options is not sufficient to infer an exact device profile or capability.
  Continue to distinguish absent, invalid, stale and unreadable values.
- A complete assurance review would additionally need runtime tests, source-map
  or unobfuscated source if available, native-library analysis, controlled
  cloud captures, firmware/version comparisons and device validation. None
  of these was performed here.

## Reproducing the static inspection

Work on a local copy in a private temporary directory; do not upload the APK or
decompiled code to a third-party service. Check the XAPK hash, unpack the
individual APKs, and extract `assets/index.android.bundle` from the base APK.
Metro's `__d` wrappers identify the numeric modules cited above; a JavaScript
formatter can make them readable without executing them. Decode the binary
Android manifest and decompile only first-party DEX classes with an Android
analysis tool such as Androguard. Compare reported results with the bytecode
when decompilation fails or the generated Java is ambiguous. Keep credentials,
device identifiers and third-party application source out of the repository.
