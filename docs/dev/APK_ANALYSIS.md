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
- A second, complete static pass on 2026-11-03 read every first-party Metro
  module (`0` and `678..851`) after pretty-printing, checked every caller of
  an exported function across the whole bundle, compared the catalog tables
  cell by cell, and parsed the first-party DEX bytecode directly (strings,
  invoked methods, field references and array payloads). It confirmed the
  existing tables and added the corrections and details in this document.
  Where a finding rests only on that pass, it says so; nothing from it was
  executed or confirmed at runtime.

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
4. The normal schedule editor gets and replaces a complete `switchPoints`
   list through dedicated cloud API endpoints. A separate selector/action
   helper for `490xx` registers is present in the bundle, but no in-bundle
   caller was found. A later two-point hardware test executed MED at Tuesday
   12:05 and HIGH at 12:10; the local `49011..49017` view remained on the
   second point even while MED was active. See **JS 682, 683, 684, 787, 788,
   789** and [MELTEM.md](MELTEM.md#two-point-schedule-execution-on-2026-11-03).
5. Service metadata exposes calibration, frost protection, flap timing,
   factory reset and other sensitive settings. Presence in the bundle does
   not justify writing them. The service menu is conditional on
   `Settings.SERVICE`; that property is not defined in the inspected default
   settings object. See **JS 696, 697, 782, 786**.
6. The extract-only and supply-only buttons (`Abluft` / `Zuluft`) keep their
   airflows **on the phone**, per device, and send both sides explicitly in
   every `41120` mode-`4` command. Only the MANUAL button uses the legacy
   `41133` write; the app never uses `41113` for its display. See
   [Runtime modes and airflow](#runtime-modes-and-airflow) and **JS 761, 783**.
7. The local gateway setup writes one 69-register block at gateway
   `41030..41098`: a 64-bit credential derived from the gateway code, the
   cloud URL with the bridge ID, and a zero GUID field. Diagnostics and tests
   must never read that range on the gateway. See
   [Gateway discovery and setup](#gateway-discovery-and-setup-on-the-local-network)
   and **JS 813**.
8. Opening the app's Settings page selects up to 20 tags one after another
   through `40911` and reads each through `40901`. This is a plausible,
   unconfirmed explanation for the changing local `40901` readings in
   [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md). See
   [Settings and parameter tags](#settings-and-parameter-tags).
9. The app labels `41000` as extract air (`Abluft`) and `41004` as exhaust air
   (`Fortluft`), pairs `41000` with extract humidity `41006`, and classifies
   sub-modes `16/80/48` (automatic), `144/208/176` (CO2) and `112` (humidity).
   Both agree with the integration's local decoding; they remain cloud-side
   metadata. See [Register metadata](#register-metadata).

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

The AsyncStorage keys are `MELTEM_SESSION_ID`, `MELTEM_USERNAME`,
`MELTEM_TIME_PROGRAMS_V112` (templates), `MELTEM_TIME_PROGRAMS_CACHE_V112`
(per-device schedule cache), and four airflow stores for the extract-only and
supply-only buttons: `MELTEM_SUPPLY_VALUES`, `MELTEM_EXHAUST_VALUES`,
`MELTEM_SUPPLY_VALUES_RIGHT` and `MELTEM_EXHAUST_VALUES_RIGHT`. The cache and
the four airflow stores are JSON objects keyed by `deviceId`. Their use is
described under [Runtime modes and airflow](#runtime-modes-and-airflow).

The lifecycle has three details that matter when interpreting app behavior:

- Login writes the session and username and also writes `''` to the schedule
  cache, so every login effectively empties the per-device schedule cache.
- The cleanup removes every key except the templates and empties the process
  globals. It runs after any `/user/logout` or `/user/delete` **response**,
  including an error response, and after a `10201` or `401` error. If the
  logout request fails at network level, nothing is cleared.
- At startup every stored value is copied into the implicit global `Globals`,
  but only the session ID and username are read later. The exported
  `getSessionId`, `setSessionId` and `removeSessionId` helpers have no caller.

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
Every request carries `apiKey`. Login, registration and the two recovery
requests carry no session; all other requests add `sessionId`, and the
account requests also add `username`.

These are observed client calls, not a promise of API stability. A successful
HTTP request is not the same as a device-side operation or a confirmed
physical state.

### Transport and error handling

**JS 683** is the only first-party code that calls `fetch`. It has three
request forms: GET, form POST (`application/x-www-form-urlencoded;charset=UTF-8`)
and JSON POST (`application/json;charset=UTF-8`). No other header is set; the
API key travels as a parameter.

- Every key and value passes through `encodeURIComponent`. Arrays become a
  comma-joined string, so `registers` and `values` arrive as
  `41000%2C41002%2C…`. Booleans arrive as `true` / `false`.
- The JSON POST used by `/device/timeprogram/set` sends a **bare JSON array**
  of switch points as the body; delete-all sends `[]`.
- There is no timeout, abort, retry or HTTP-status check. Every response is
  parsed with `.json()`.
- Errors are read from the JSON body field `error`, not from the HTTP status.
  `error == 10201` triggers a **local** logout: storage cleanup and navigation
  to the login screen, without a `/user/logout` request. `error == 401` does
  the same and then shows "App outdated / Please update the app". In both
  cases neither the success nor the error callback of the request runs.
- An exception thrown inside a success callback is caught by the same promise
  chain and passed to the error callback.

The action layer (**JS 682**) treats a response as successful only under
these conditions: login needs `sessionId`, registration needs `userId`, a live
read needs no `error` and a `data` field, and a schedule read needs
`switchPoints`. A completed explicit read passes `data` on as an array of
`{address, value}` objects, optionally with a per-register `status`.

### Endpoints

`Session` below means `sessionId`; `apiKey` is always present and omitted.
**Called** states whether any first-party code in 1.17 calls the wrapper.

| Method and path under `/v1` | Additional fields | Purpose | Called |
| --- | --- | --- | --- |
| `POST /user/auth` | `username`, `password` | Log in. | yes |
| `GET /user/details` | session, `username` | Account details including `language`. | yes |
| `POST /user/logout`, `POST /user/delete` | session, `username` | End session or delete account. | yes |
| `POST /user/register` | `username`, `password`, `email`, `languageCode`, `validationRequired=true` | Register an account. | yes |
| `POST /user/updatePassword` | session, `username`, `password`, `newPassword` | Change password. | yes |
| `POST /user/updateEmail` | session, `username`, `password`, `newEmail` | Change email. | yes |
| `POST /user/updateLanguage` | session, `username`, `languageCode` | Set account language. | yes |
| `POST /user/resetPassword` | `username` | Start password recovery. | yes |
| `POST /user/recoverUsername` | `email`, `languageCode` | Start username recovery. | yes |
| `POST /bridge/register` | session, `macAddress` | Register a gateway; returns `bridgeId`. The MAC is sent as 12 lower-case hex digits without separators. | yes |
| `GET /bridge/list` | session | List gateways. | yes |
| `GET /bridge/devices` | session, `bridgeId` | List bound units. | yes |
| `GET /bridge/products` | session, `bridgeId` | List bindable products. | yes |
| `POST /bridge/info`, `GET /bridge/info/status` | session, `bridgeId`, then `statusId` | Gateway information. | **no** |
| `POST /bridge/unbind` | session, `bridgeId` | Unbind a gateway. | yes |
| `POST /bridge/bindProduct` | session, `bridgeId`, `productId`, `serialNumber`, `skipP2P` | Bind a unit. A second wrapper without `serialNumber` and `skipP2P` has no caller. | yes |
| `GET /bridge/bindProduct/status`, `POST /bridge/bindProduct/abort` | session, `bridgeId`, `statusId` | Poll or abort binding. | yes |
| `POST /bridge/zone/create`, `GET /bridge/zone/list` | session, `bridgeId`, `name` | Create or list zones. | **no** |
| `POST /bridge/zone/update`, `POST /bridge/zone/delete` | session, `bridgeId`, `zoneId`, `name` | Update or remove a zone. | **no** |
| `POST /bridge/updateName` | session, `bridgeId`, `name` | Gateway name. | yes |
| `POST /bridge/setTimeZone` | session, `bridgeId`, `daylightSavingType`, `timeZoneOffset` | Gateway time zone. | yes |
| `GET /device/data/live` | session, `deviceId`, `registers` | Cached/live values. | yes |
| `POST /device/data/get`, `GET /device/data/get/status` | session, `deviceId`, `registers`, then `statusId` | Explicit register read and its status. | yes |
| `POST /device/data/set` | session, `deviceId`, `register` (start address), `values` | Register write. | yes |
| `GET /device/data/set/status` | session, `deviceId`, `statusId` | Write status. | **no** |
| `GET /device/unbind`, `GET /device/unbind/status` | session, `deviceId`, then `statusId` | Unbind a unit and poll. | yes |
| `POST /device/zone/set` | session, `deviceId`, `zoneId` | Assign a zone. | **no** |
| `POST /device/updateName` | session, `deviceId`, `name` | Unit name. | yes |
| `GET /device/timeprogram/get` | session, `deviceId` | Get switch points. | yes |
| `POST /device/timeprogram/set` | query: session, `deviceId`; body: JSON array | Replace the schedule. | yes |
| `GET /device/timeprogram/set/status` | session, `deviceId`, `statusId` | Poll schedule write. | yes |

Nine wrappers are dead in 1.17. The zone wrappers are also broken: they call
`encodeIconZone` / `decodeIconZone`, which exist nowhere in the bundle, so a
call would throw. Zone-like grouping in the app is done only through the
`place~area~name` naming convention.

### Cloud error codes

`getErrorMsg` (**JS 684**) looks up the `error` field in the string table of
**JS 686** and returns an empty string for unknown codes. English meanings:

| Code | Meaning | Code | Meaning |
| ---: | --- | ---: | --- |
| `401` | Internal application error | `10316` | Generic invalid parameter |
| `10001` | Internal service exception | `10322` | Zone does not exist on this gateway |
| `10103` | Username and password do not match | `10324` | Invalid zone name |
| `10104` | Username already exists | `10330` | Invalid gateway name |
| `10106` | Invalid user entry (no special characters) | `10401` | User not allowed to access this device |
| `10108` | Account not activated; email sent | `10410` | Device not found |
| `10201` | Invalid session ID | `10415` | Zone not found |
| `10301` | User not allowed to access this gateway | `10420` | Register not configured settable |
| `10310` | Gateway not found | `10430` | Values are not valid |
| `10312` | Gateway not ready; may never have been connected | `10520` | Register not configured gettable |
| `10314` | Product cannot be bound | | |

`10420` and `10520` show that the cloud keeps per-register allow-lists for
reading and writing. This is one more reason not to treat cloud register
numbers as local Modbus holding registers.

### Status codes and polling

**JS 682** expects status `5000` for a completed data read, `2000` for a
completed data write, and `6000` for a completed schedule write. A pending
read can report `105`; `5030` causes one retry in one settings-read path, and
`6030` terminates the schedule-write wait as failure. Despite the separately
defined `data/set/status` route, the inspected `readWriteStatus` helper calls
the **data/get/status** wrapper and checks `2000`. This is a property of the
client code, not proof that all server versions require that route.

The polling helpers behave as follows:

- **Write status (`readWriteStatus`).** The first poll follows the write
  immediately. After each unsuccessful poll the helper sleeps 25, 50, 100, …
  3200 ms, including a final, wasted sleep after the eighth poll: at most
  eight requests and 6375 ms of sleep. Its failure callback is empty and it
  returns nothing, so callers continue whether the write was confirmed or
  not. With an empty `statusId` it sends no request but still sleeps the full
  6375 ms.
- **Read status.** Callers poll `data/get/status` with the same eight-step
  doubling. `5000` is success and `105` means pending.
- **Schedule write status.** The first poll is immediate; afterwards the
  helper sleeps 100 ms plus the request time between polls. `6000` is success
  and `6030` failure. Any other status, a JSON error or a network error keeps
  polling until 120 s of wall-clock time have passed. With an empty
  `statusId` it waits the full 120 s and then reports failure.
- **Several units.** `asyncForEachPerBridge` (**JS 684**) groups units by
  `bridgeId`, runs each group strictly in sequence and starts the groups
  concurrently. Its completion callback fires only when every group has
  finished; never for an empty list or when an item rejects. Several paths do
  not use it (see the settings, list and schedule sections below).
- **No rate limit.** The client has no global request queue. The single-unit
  overview uses a 3 s `setInterval`, which fires whether or not the previous
  request has finished; the multi-unit overview does not poll at all.
- **Unknown products are hidden.** `getBridgeDevices` keeps only units whose
  cloud `productId` is a key of the catalog in **JS 696** (`0001c874`,
  `0001c853`). Other units disappear from the device list and the binding
  screen.

### Client constants

**JS 697** exports these constants. The app also creates the implicit
globals `Settings` (`API_URL`, `API_WEBSOCKET_URL`, API key,
`DISABLE_WARNINGS: true`) and `Globals` (`SESSION_ID`, `USERNAME`).
`DISABLE_WARNINGS` makes the entry point (**JS 0**) suppress all React Native
warning boxes.

| Constant | Value | Use |
| --- | ---: | --- |
| `POLLING_TIMEOUT` | 3000 ms | Interval of the single-unit overview and of the unbind, gateway-setup and binding status polls. |
| `MAX_POLLING_INTERVAL_UNBIND` | 30000 | Unit unbind fails after more than 10 polls. |
| `MAX_POLLING_INTERVAL_BRIDGE_SETUP` | 100000 | Gateway setup fails on the 34th poll (about 102 s). |
| `MAX_POLLING_INTERVAL_BINDING` | 60000 | Binding fails on the 21st poll (about 63 s). |
| `BRIDGE_DISCOVERY_TIMEOUT` | 60000 ms | UDP gateway discovery stops after 60 s. |
| `STATUS_LOOP_NUM` | 8 | Maximum status polls per request. |
| `STATUS_LOOP_SLEEP_INITIAL` | 25 ms | First sleep between status polls, doubled each time. |
| `TIME_PROGRAM_STATUS_TIMEOUT` | 120000 ms | Wall-clock limit of the schedule-write poll. |
| `TIME_PROGRAM_GET_STATUS_TIMEOUT` | 100 ms | Despite the name, the sleep between schedule-status polls. |
| `BRIDGE_REFRESH_TIMEOUT` | 600000 ms | The device list reloads gateways, units and error states every 10 minutes. |
| `MAX_TIME_PROGRAM_SAVE_LOOPS` | 3 | Schedule save rounds in total, including the first. |
| `BRIDGE_STATUS_INTERVAL` | 1000 ms | Never read. |
| `MESSAGE_VIEW_INTERVAL` | 5000 ms | Never read. |
| `IOS_ALERT_DELAY` | 800 ms | Never read; one screen hard-codes 800 ms instead. |
| `SET_REGISTER_SLEEP` | 300 ms | Never read. |
| `RF_TIME_PROGRAM_WORKAROUND` | `true` | Never read. |

`SET_REGISTER_SLEEP` is therefore not a Meltem-recommended delay between
writes, and nothing in the app enables the `490xx` RF workaround described
under [Weekly schedules and templates](#weekly-schedules-and-templates). All
live timings are cloud and UI timings; they are not appropriate defaults for
this integration's sensitive local bus.

## Gateway discovery and setup on the local network

**JS 805, 812, 813, 818, 821, 823** and the native class
`com.meltem.bridgeSetupModule.BridgeSetupModule` with its `TcpClient` show the
following setup path. All Modbus addresses in this section are **gateway**
addresses on unit ID `1`, not room-unit registers with the same numbers.

1. The explanation screen requires the phone's network type to be Wi-Fi and
   asks for gateway power over Micro-USB, an Ethernet cable to the router and
   the phone on the same network.
2. Discovery starts only after `GET /bridge/list` has succeeded. The app then
   binds UDP port `35353` on all addresses and enables broadcast reception; it
   never sends a UDP datagram. The message content is ignored: the sender IP
   of a datagram triggers one local probe, but only if that IP has not been
   probed yet and no other probe is running. Datagrams that arrive during a
   probe are dropped, not queued, and an IP whose probe failed is not retried
   while the screen is open. After 60 s the socket is closed; probe results
   that arrive later are lost. If the bridge-list request fails, the socket is
   never bound and the screen simply times out.
3. Each probe is one native TCP connection to port `502`: it writes the MAC
   read frame, reads once, writes the URL read frame, reads once and closes.
   The frames are built in JavaScript (**JS 813**) and are listed below.
4. An empty URL (all zero bytes) is classified as ready for setup. A URL that
   **contains** `wss://gw.connect2myhome.eu/meltem?bridgeId=` is cut after
   that prefix, and the remainder is compared with the account's bridge list
   to classify it as this account's or another account's setup. A gateway is
   listed only when both responses have exactly the expected length and its
   URL is empty or such a Meltem URL; a gateway configured with any other URL
   does not appear. The classification is computed but **never displayed**:
   each list entry shows only MAC and IP, and every listed gateway goes through
   the same setup flow without a confirmation step.
5. The user scans the gateway's QR code or types its code. The code is checked
   locally (format below) before the wait screen opens; it is not a cloud
   password and is not sent to the cloud.
6. The wait screen registers the MAC with the cloud, receives a `bridgeId`,
   and immediately sends the local function `0x10` write described below.
   Cloud polling for the gateway runs **concurrently** with that write, not
   after its response; the write response can only abort the setup.
7. After success the app sets daylight-saving type `4` and time-zone offset
   `60` before the naming screen. This is what the client sends, not proof of
   the server's interpretation or a recommendation for other time zones.
   It matches the gateway values `41021 = 4` and `41022 = 60` observed in
   [MELTEM.md](MELTEM.md#read-only-spot-check-of-pyairios-candidates-2026-10-21),
   which supports, but does not prove, that the cloud writes these registers.

### Gateway Modbus TCP frames

Every frame uses the MBAP header `00 06 00 00 <length>`: a **fixed transaction
ID of 6**, protocol ID 0, and a correct length for unit ID plus PDU. The unit
ID is always `0x01`.

| Purpose | Function | Start address | Quantity | Frame size |
| --- | --- | --- | --- | ---: |
| Read gateway MAC | `0x03` | `0xA034` / `41012` | 3 registers | 12 bytes |
| Read cloud URL | `0x03` | `0xA04A` / `41034` | 57 registers (114 bytes) | 12 bytes |
| Write setup block | `0x10` | `0xA046` / `41030` | 69 registers, byte count 138 | 151 bytes |

The 69-register setup block is laid out as follows:

| Gateway registers | Bytes | Content |
| --- | ---: | --- |
| `41030..41033` | 8 | The 64-bit credential decoded from the gateway code, **least-significant 16-bit word first**, each word big-endian. |
| `41034..41090` | 114 | ASCII `wss://gw.connect2myhome.eu/meltem?bridgeId=<bridgeId>`, zero-padded at the end. |
| `41091..41098` | 16 | The `GUID` field, always 16 zero bytes. |

The word order of the credential matches the gateway's low-word-first
convention for 32-bit values described in
[MELTEM.md](MELTEM.md#network-settings-mac-address-and-uptime-width).
The padding helper never truncates, so a URL longer than 114 bytes would shift
the GUID field; that cannot happen with the fixed URL format.

Responses are parsed without checking transaction ID, unit ID, function code
or byte count. The parser removes non-word characters from the hex string,
drops the first 9 bytes, and accepts the remainder only if it has **exactly**
the expected length: 6 bytes for the MAC, 114 bytes for the URL. A response
with extra bytes, or one that arrives in several TCP segments, is therefore
rejected. For the write it expects 3 bytes after the 9-byte skip, which a
normal 12-byte function-`0x10` echo provides. A 9-byte Modbus exception leaves
nothing and is reported to the user as a wrong gateway code, whatever the
exception code; an empty native result is reported as gateway not available.

The setup block contains a credential and the cloud binding. Whether gateway
`41030..41098` can be read back is untested. Integration diagnostics, raw
register dumps and live tests must never read that range on the gateway (unit
ID `1`). On room units, `41030..41033` are the unrelated operating-hour
registers.

### Gateway code format

The gateway code is decoded and checked in **JS 813**; the native
`ModbusProtocol` class contains the same algorithm. This describes the
structure only; no real code is reproduced here.

- **QR payload.** If the scanned text contains `?P`, the code is the first
  `?`-separated segment that starts with `P`, without that `P`. Otherwise the
  whole text is used. The camera accepts Aztec, QR and DataMatrix codes.
- **Normalization.** Every character except `A–Z`, `a–z` and `0–9` is
  removed. An empty result asks for a code.
- **Alphabet.** A case-insensitive, Crockford-style Base32 alphabet with 32
  symbols: digits `0–9` are 0–9, `O` is also 0, `I` and `L` are also 1, and the
  letters `A–Z` without `I`, `L`, `O` and `U` are 10–31 in alphabetical order.
  `U` and any other character are skipped.
- **Length.** The first 14 valid symbols are used; further symbols are
  ignored, and fewer than 14 make the code invalid.
- **Bits and check.** The 14 symbols form 70 bits, most significant first. The
  top 6 bits are a check value, the low 64 bits the credential. The code is
  valid when the unsigned credential modulo 37 equals the check value.
- **Use.** The credential is written to the gateway as the first four
  registers of the setup block. `POST /bridge/register` sends only the MAC.
  That the cloud already knows the credential, for example by MAC, is an
  inference that the app does not show.

### Setup timing and failure handling

The wait screen polls every 3 s once a `bridgeId` exists. Each poll calls
`GET /bridge/list`; when the gateway is listed it calls `GET /bridge/devices`
for it, and a response without `error` counts as success. A listed gateway
with a `devices` error, or a gateway that is not listed yet, keeps polling. A
list error or a network failure aborts the setup. The setup times out on the
34th poll, about 102 s after registration.

Failure paths have three gaps:

- A network failure of `POST /bridge/register` has no error handler, so
  `bridgeId` stays empty, the poll never counts and the screen spins forever.
- A failed `setTimeZone` request shows the generic setup error although the
  gateway is already registered.
- The JavaScript layer listens for a `MODBUS_CONNECTION` event that the native
  module never emits, and wraps a `discoverModbusStop` method that the native
  module does not implement.

### Gateway naming, result screens and unbinding

- **Naming** sends `POST /bridge/updateName`. Saving is disabled for an empty
  name; there is no length limit in the client, so the 20-character limit of
  the [app manual](reference/app.md#prerequisites-and-setup) is not enforced
  there.
- **Result screens.** Success offers to add products now (unit QR scan) or
  later; the error screen offers to detect the gateway again.
- **Unbinding a gateway** sends one `POST /bridge/unbind` and treats a
  response with `bridgeId` as success. There is no status poll and **no local
  Modbus write** that clears the URL or credential, so the gateway's setup
  block presumably stays in place after an app unbind.

### Native setup module

`BridgeSetupModule` exposes exactly three methods to JavaScript:
`discoverModbus(ip, macFrame, urlFrame)`, `writeServerConfig(ip, frame)` and
`tcpPing(address, port)`. Results return as the events
`MODBUS_DISCOVERY_DATA` (`discoveredIp`, `discoveredMac`, `discoveredUrl`,
as upper-case hex strings) and `MODBUS_SERVER_DATA` (`bridgeServerResult`).
The separately present native `UDPClient` (with its `modbus` text filter)
and native `ModbusProtocol` are not reachable from these three methods. The `TcpClient` sets no connect or read timeout in the parsed
bytecode, so platform socket defaults apply.

The inspected native TCP path uses ordinary TCP, not TLS; setup credentials
therefore require a trusted local network. The code reads a response with
individual socket reads; this static analysis does not prove correct handling
of fragmented TCP responses. No gateway setup writes should be tried as part
of normal integration discovery.

The expression in **JS 805** assigns `"ios"` to `Platform.OS` before invoking
`tcpPing('255.255.255.255', 8052)`, rather than comparing it. The Android
native `tcpPing` method only logs. Because `Platform` is a plain, unfrozen
object, the assignment probably persists for the rest of the JavaScript
session, so later first-party checks could take their iOS branches on
Android (keyboard handling, an 800 ms alert delay, the time picker and the
camera permission path). The actual impact requires runtime verification; it
is not evidence that an Android broadcast ping is sent.

## Unit binding and unbinding

**JS 825–830** and **JS 785** bind and unbind units through the cloud. No
local Modbus traffic is involved.

### Unit QR code

The unit QR code (**JS 827**) is split on `?`; the segments may come in any
order:

| Segment | Parsed as |
| --- | --- |
| Starts with `R` | Serial number: the rest of the segment. It is sent as `parseInt(serial, 16)`, i.e. the hexadecimal code as a decimal integer. |
| Matches `Vh<digits>s<digits>` | `versionNumber`: the digits after `s`. The meaning of `h` and `s` (presumably hardware and software) is not established. |
| Contains a catalog `nameOfficial` | Product: `VMD-22RPS44` selects `0001c874` (M-WRG-II), `VMD-22RES22` selects `0001c853` (M-WRG). |

All three values are required, and the version must be nonzero; otherwise the
app asks to rescan or to enter the serial number manually. A valid QR code
skips the product-type screen. Manual entry asks only for the serial (the
German text calls it a 6-digit code), parses it as hexadecimal without
validation, leaves `versionNumber` at 0, and then asks for the product type
from `GET /bridge/products`, filtered to the two catalog IDs.

### Binding request and `skipP2P`

`skipP2P` is true when the product is `0001c874` with a version below 22, or
`0001c853` with a version below 28. Manual entry therefore always sends
`skipP2P=true`. In that case the app shows a warning on entry and the stricter
instruction that all other units within the gateway's range must be switched
off; otherwise it only recommends this. Both catalog entries have
`incomingBinding: false`, so binding starts only when the user taps the bind
button. The instructions tell the user to cut the unit's power for 5 s; the
unit then stays in binding mode for 5 minutes, which matches the remote-control
manual (see [reference/app.md](reference/app.md#prerequisites-and-setup)).

`POST /bridge/bindProduct` returns a `statusId`. The app polls
`GET /bridge/bindProduct/status` every 3 s and gives up on the 21st poll:

| Status | Handling |
| --- | --- |
| `100`, `103`, `110` | Error: check the gateway's internet connection. |
| `3000` | Success. After 4 s the app reads `GET /bridge/devices`. If the new unit's `productId` differs from the selected product, it sends `GET /device/unbind` without waiting and reports a wrong device type; if the unit is missing, it reports an internal error. Otherwise it opens the naming screen. |
| `3030` | Error with `errorCode`, see below; a missing or unknown code gives a generic binding error. |
| `3040` | No device found in binding mode. |
| Other | Keep polling. |

Every error, and the header's cancel button while a `statusId` exists, sends
`POST /bridge/bindProduct/abort`. A rejected status request stops polling
with an empty message.

| `errorCode` with status `3030` | English meaning |
| --- | --- |
| `1`, `3` | Binding initiated but not finished |
| `10` | Binding unexpectedly closed |
| `100` | Could not communicate; check that the unit is not already bound and is in binding mode |
| `101` | Device not compatible |
| `102`, `778` | Maximum number of devices reached |
| `103` | Communication error |
| `104` | Binding stopped |
| `105` | Device ID not valid |
| `200` | Generic binding error |
| `201`, `782` | Unknown product type |
| `771` | Internal error |
| `773` | Gateway error |
| `775` | Another binding in progress |
| `784` | Incorrect product type |

This refines the app manual's statement that a wrong manually selected type
prevents the connection: the cloud binding can succeed, after which the app
compares the product, unbinds the unit and shows an error.

After a successful binding the app asks the user to wait a few minutes while
the unit is configured, to ignore failures during that time, and notes that
power-cycling the new unit shortens it.

### Naming

Units are named `place~area~name`. Grouping is optional behind an options
button; when it is enabled, place and area are both required, and existing
places and areas are offered as suggestions. `POST /device/updateName` stores
the name; there is no length limit. Renaming from the device list does not
pre-fill place or area, so saving without the options stores a plain name and
moves the unit to the "Others" group.

### Unit unbinding

`GET /device/unbind` returns a `statusId`; the app polls
`GET /device/unbind/status` every 3 s and fails after more than 10 polls:

| Status | Handling |
| --- | --- |
| `4000` | Success. |
| `100`, `103`, `110` | Error: check the gateway's internet connection. |
| `4030` | Error while removing the product. |
| `4040` | No product found to unbind. |

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

Each product entry also has `type: 'ventilation'`, `incomingBinding: false`
(binding starts only on user action), `nameOfficial` (used to recognize the
unit QR code), `requestRegisters: ['41027']`, `onlineStatusRegisters:
['40101']` and `errorRegisters: ['40103', '41016', '41017']`. The catalog
object is assigned to an undeclared global. Units whose cloud `productId` is
not one of the two keys are hidden from the app entirely (see
[Status codes and polling](#status-codes-and-polling)).

The setting pages are selected through `settingTypes`
(`tagsService`, `tagsSettings`, `tagsInterval`, `tagsTemperature`,
`tagsNight`). The ordinary settings list is the concatenation of five groups:

| Group | M-WRG-II (`0001c874`) | M-WRG (`0001c853`) |
| --- | --- | --- |
| Boost | `11, 12` | `11, 12` |
| Control panel | `13, 14, 15, 44, 45` | `13, 14, 15` |
| Cross air | `43, 42, 47, 46` | `43, 42, 47, 46` |
| Two sensors | `151, 152, 153, 154` | empty list |
| Others | `7, 8, 9, 10, 76` | `7, 8, 9, 10, 76` |
| Interval page | `67, 66, 69, 68` | `67, 66, 69, 68` |
| Temperature page | `65, 81, 82` | `65, 81, 82` |
| Night list | `58, 60, 61` | `58, 60, 61` |

No screen navigates to the night list; see
[What the app settings screen does](#what-the-app-settings-screen-does).

### Register metadata

The following is the complete register-key inventory in **JS 696**, grouped
where neighbouring entries share a purpose. `R`, `W` and `RW` are the app's
labels, not independently established Modbus access rights.

| Key | App purpose | Access / caveat |
| --- | --- | --- |
| `40101` | RF communication problem | R; used for online-status checks. |
| `40103` | Unit fault | R; included in error checks. |
| `41000`, `41002`, `41004` | Exhaust (`Abluft`), inlet and outlet temperatures | R; metadata range `-200..200`, UI uses strict bounds. The app's labels and its absolute-humidity pairing agree with the observed local mapping; see below. |
| `41006`, `41011` | Exhaust and supply humidity | R; metadata min 1, max 100. |
| `41007`, `41012` | Exhaust and supply CO2 | R; metadata min 0, max 32766; the integration uses ppm, exposes `41007` on CO2-capable profiles and `41012` only on the VOC profile, disabled by default. |
| `41008`, `41013` | Exhaust and supply VOC | R; metadata says ppm, range `0..32766` as for CO2; the integration uses ppm on the VOC profile, with `41008` disabled by default. |
| `41009` | Supply temperature | R; same temperature metadata. |
| `41015` | Ventilation-speed capability bits | R. |
| `41016`, `41017` | Fault code, dirty-filter flag | R; included in error checks. |
| `41019` | Actual speed mode | R; marked `supported: '0001c853'`, a field that no code reads. |
| `41020`, `41021` | Exhaust and inlet fan speed | R; app labels percent, the overview shows the raw value without a unit on the extract and supply buttons. |
| `41027`, `41030` | Filter time remaining and operating hours | R; days and hours respectively. |
| `41100..41102` | Current mode and submodes | R; **key `41100` erroneously contains `register: 41101`** in metadata. The code looks metadata up by key and never reads the `register` field, so the error has no visible effect. |
| `41103..41105` | Temporary override mode and submodes | R. |
| `41106` | Scheduler state | R. |
| `41113`, `41133` | Legacy speed/mode read and write | R and RW respectively; see [the legacy mode word](#legacy-mode-word-41113--41133). |
| `41120..41125` | Set mode, submodes and temporary override | W. |
| `42000..42005` | Humidity and CO2 thresholds and minimum/maximum levels | RW; the six are requested as settings. |
| `42006..42009` | Priority override mode, level, delay and run-on time | RW metadata only; not in the normal six-register settings list. `42006` has the same name as service tag `95`; see [Service catalog inventory](#service-catalog-inventory). |
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

The integration follows the APK mapping and exposes all four CO2/VOC register
values as ppm measurements on profiles with the matching sensor capability.
No positive reading from a sensor-equipped unit has yet been captured.

### Overview display

The single-unit overview (**JS 782, 783**) polls `GET /device/data/live` every
3 s for the active list above plus the three error registers. A poll error is
ignored; if the first poll never succeeds, the spinner stays. A multi-unit
selection does not poll and shows no values.

- **Temperature labels.** `41000` is shown in the `Abluft:` / `Exhaust:`
  section, `41004` as `Fortluft Temp.` / `Outlet temp.` and `41002` as
  `Außenluft Temp.` / `Inlet temp.`; `41009` is supply. The app thus treats
  `41000` as extract air and `41004` as exhaust air. That agrees with the
  observed local swap in
  [MELTEM.md](MELTEM.md#temperature-register-quirk) and differs from the
  Modbus manual. It is cloud-side labelling, not a local measurement.
- **Absolute humidity** is `6.112 · exp(17.67·T / (T + 243.5)) · RH · 2.1674 /
  (273.15 + T)` in g/m³, computed from `41000` with `41006` for extract air
  and from `41009` with `41011` for supply air, only when both values pass the
  validity check. The integration uses the same Magnus formula.
- **Precision.** Temperatures and absolute humidity are shown with two
  decimals; operating hours (`41030`) have no unit label.
- **Filter.** The colour is normal only when `41027 > 10`; ten days or less,
  or a missing value, use the "expired" colour. `0` shows "Air filter change
  required"; other values show "Air filter change in {n} days".
- **Sections.** The extract section appears when `41000`, `41006` or `41007`
  is valid, the supply section for `41009`, `41011` or `41013`, and "Others"
  for `41004`, `41002` or `41030`. Fan speeds, `41027` and `41106` have no
  range metadata and are not checked.
- **Error registers are polled but never shown.** `40103`, `41016` and
  `41017` are merged into the screen state, but no rendering code reads them,
  and the helper that would list "Device Fault", "Fault Code" and "Dirty
  Filter" has no caller. The error dialog shows only the message passed in
  from the device list; its "Service" button opens the contact screen.

### Device list and error check

The device list (**JS 769, 777**) does not use the live endpoint. For every
unit on every gateway, **in parallel** and without per-gateway grouping, it
starts an explicit read job:

1. `POST /device/data/get` for `40101`, then up to eight status polls.
2. Only if that read completes with `5000` and yields no message, a second job
   reads `41027`, `40103`, `41016` and `41017`.

`getDeviceErrorMsg` (**JS 684**) serializes the response with
`JSON.stringify` and tests for substrings:

| Substring found | Result group | Message |
| --- | --- | --- |
| `40103,"value":1` or `41016,"value":1` | Error | Restart the device; contact Meltem if the error persists. |
| `41017,"value":1` | Filter | Filter change required (appended to a device error). |
| `40101,"value":1`, `"status":105`, `"status":110` or `"status":5030` | Offline (first job only) | Check the gateway and device connection. |

Device and filter messages take precedence over the connection message.
Because these are prefix tests, values such as `10..19` would also match; for
0/1 flags this is equivalent to `== 1`. The rule also assumes that the server
serializes `address` immediately before `value`, which was not observed. A
`105` that persists through the eighth poll of the first job lists the unit as
offline; in the second job it is ignored. `41027` is requested but not used
here.

The list reloads gateways, units and error states every 10 minutes, on
pull-to-refresh, from the home icon and when the screen is created without
gateways. Unit names with exactly three `~`-separated parts are grouped into
place and area; all others go to "Others". Selecting a place or area selects
every unit whose name starts with that prefix, across gateways; selecting
"Others" selects only names without any `~`, so names with one or more than
two separators are listed there but left out of the selection.

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

These are **runtime commands**, not the saved values displayed in the settings
pages. The app's setting catalog separately assigns tag `11` to intensive
airflow, `12` to intensive duration, and `13`, `14`, `15` to the stored
keypad LOW, MED and HIGH airflow values. Editing those tags does not send the
corresponding `41120` quick-mode command.

For the unbalanced UI path, a positive slider value is encoded as
`round(value / 10) + 200`; zero remains zero. The controls use 10-unit UI
steps. The metadata also lists continuous submodes `201..210`, automatic
submodes `16, 80, 48`, and CO2 submodes `144, 208, 176`; that does not
establish every variant's meaning. `208` occurs in both the CO2 and the
continuous list; only the mode word (`2` or `3`) separates them. The
`NIGHT` array is never sent by reachable code; the interval and temperature
arrays are sent only by the Start buttons of their settings pages.

### Mode buttons and displayed state

The control component (**JS 761**) shows extract and supply buttons with the
raw `41020` / `41021` values, a MANUAL button above LOW, MED and HIGH, and a
second row with AUTO, CO2, RH, BOOST and OFF. Its inputs come from **JS 783**:

- `actualMode` is `41100`, or `41103` when `41100` is `0`.
- `actualSubMode` is `41101`, or `41104` when `41101` is `0`, independently of
  the mode fallback.
- `actualSubModeCon` is `41102`; there is no fallback to `41105`.

A button lights up under these rules:

| Button | Condition |
| --- | --- |
| LOW / MED / HIGH | Mode `3` with sub-mode `228` / `229` / `230`. |
| AUTO | Mode `2` with sub-mode `16`, `80` or `48`. |
| CO2 | Mode `2` with sub-mode `144`, `208` or `176`. |
| RH | Mode `2` with sub-mode `112`. |
| BOOST | Mode `3` with sub-mode `227`, typically through the `41103/41104` fallback. |
| OFF | Mode `1` with sub-mode `0`. |
| MANUAL | Mode `3` with sub-mode `201..210`. |
| Extract button | Mode `4` and `41102` greater than the supply sub-mode. |
| Supply button | Mode `4` and the supply sub-mode greater than `41102`. |

With equal sides in mode `4`, for example `[4, 205, 205]`, no button lights
up. Night (`3/226`), interval (`3/231`) and temperature (`2/224`) light no
button; the overview shows them as a text banner, evaluated from
`41100/41101` only. The BOOST fallback is consistent with the observed local
`41100..41102 = [0, 0, 0]` during intensive ventilation; see
[HW-1](HARDWARE_BACKLOG.md#hw-1--intensive-shadow-registers-survive-a-power-off-write).

Gating and limits:

- **AUTO needs both sensors.** AUTO is disabled when either the humidity
  sentinel (`41006 = 239`) or the CO2 sentinel (`41007 = 32767`) is present;
  CO2 and RH depend only on their own sentinel. On the surveyed sensorless
  units the app therefore disables AUTO, CO2 and RH. A multi-unit selection
  has no sensor flags and enables all modes.
- **BOOST cannot be scheduled.** The schedule editor always disables BOOST.

### Extract-only and supply-only airflows

The extract and supply buttons (`Abluft` / `Zuluft`) are not the unit's
stored key-4 or InControl settings. They use airflows that the app keeps **on
the phone**:

- Each button has its own pair of values, stored per `deviceId` in
  AsyncStorage: `MELTEM_SUPPLY_VALUES` / `MELTEM_EXHAUST_VALUES` for the
  extract button and the `_RIGHT` variants for the supply button.
- Both dialogs set **both** sides from 0 to 100 in steps of 10, so an
  extract-only setting can have a nonzero supply airflow.
- Every command carries both values:
  `[4, s > 0 ? round(s / 10) + 200 : 0, e > 0 ? round(e / 10) + 200 : 0, 0 ×10]`.
  The app does not rely on an airflow stored on the unit.
- Without stored values the dialog starts from the current measured speeds,
  rounded to 10 and limited to `0..100`. Values are saved when a slider is
  released; cancelling the dialog afterwards does not undo the save.
- A multi-unit selection stores and reads the values under the first unit's
  `deviceId`. The schedule editor passes no `deviceId`, so its values are
  stored under the key `"undefined"` and shared by all units.

The Set button is disabled when both **current** values are zero, but the
command is built from the **stored** values. Stored values of `0/0` with
running fans could therefore send `[4, 0, 0, …]`; conversely, with both fans
at zero the button stays disabled until a slider is moved. What a unit does
with `[4, 0, 0]` was not checked.

This settles the app side of
[TODO-1](TODO.md#todo-1--shortcut-configuration-in-the-shadow-ranges-511xx--520xx):
the app's own shortcut airflows live on the phone and are sent with each
command. Whether the unit treats `200 + n` linearly over the whole range is
still the open question in
[HW-3](HARDWARE_BACKLOG.md#hw-3--ambiguous-encoding-above-200-in-the-unbalanced-target-registers).

### Legacy mode word `41113` / `41133`

The older control path writes `41133`: `(round(speed / 10) << 12) |
(modeCode << 8) | 169`. In 1.17 **only the MANUAL button** reaches it, with
mode code `1` (`OM_CONTINUOUS_SMT_SETTING`). The extract and supply buttons
always set the unbalanced type and send `41120 [4, …]`; the branch that would
send cross-air codes through `41133` cannot be reached. The existence of this
cloud path is **not** evidence that writing local `41133` is supported or
safe. The values the MANUAL dialog can send are:

| Airflow | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 90 | 100 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `41133` | 4521 | 8617 | 12713 | 16809 | 20905 | 25001 | 29097 | 33193 | 37289 | 41385 |

The MANUAL slider runs from 10 to 100 in steps of 10 and starts from the
**supply** speed `41021`, rounded to 10. The slider shows a stored value when
one exists, but Set sends the starting value unless the slider was moved.
Whether the cloud translates the `41133` write into the mode-`3` /
`201..210` state that the MANUAL button checks is not shown by the app.

The metadata decodes the same word layout for both registers:

| Bits | Meaning |
| --- | --- |
| `15..12` | Speed step `0..10` (identity map). |
| `11..8` | Mode: `0` `OM_OFF_SMT_NONE`, `1` `OM_CONTINUOUS_SMT_SETTING`, `2` `OM_BOOST_SMT_BOOST`, `3` `OM_BOOST_SMT_END`, `4` `OM_CROSS_AIR_SMT_IN`, `5` `OM_CROSS_AIR_SMT_OUT`, `6` `OM_AUTOMATIC_SMT_RH`, `7` `OM_AUTOMATIC_SMT_C02` (sic), `8` `OM_AUTOMATIC_SMT_FULL`, `9` `UNKNOWN`. |
| `7..0` | `169` (`0xA9`) in the write; ignored by the decoder. |

Both entries also carry an unused `buttons` map `{0: 5, 1: 261, 2: 517,
3: 773, 4: 1029}`, i.e. `(n << 8) | 5`; its meaning is not established.

The overview decodes `41113` on every render but **discards the result**, and
the control component never receives it. `41113` therefore has no effect on
the 1.17 display; the documented "paired read decoding" is dead code. The
encoder and decoder are named after `RES22` (the M-WRG product identifier) but
run for both products.

Local values fit the app's layout as a correlation, not as established
semantics: `425` (`0x01A9`) on units in LOW, MED or HIGH decodes to mode `1`
with speed `0`, and `1449` (`0x05A9`) on slave `4` while it reported
`[4, 0, 228]` decodes to mode `5` (`OM_CROSS_AIR_SMT_OUT`); see
[MELTEM.md](MELTEM.md#app-derived-register-survey-on-2026-11-03) and
[the slave 4 read](measurements/2026-10-09/pe-compare-slave4.txt). The
follow-up is [HW-12](HARDWARE_BACKLOG.md#hw-12--legacy-mode-word-41113).

### Time-program switch

Schedule activation/deactivation uses a 13-element `41120` write with element
at offset 6 set to `1` / `3`. The overview interprets scheduler-state values
`1`, `2`, or `4` as active. Do not infer the meanings of all other values.
The frame `[0, 0, 0, 0, 0, 0, 1 or 3, 0, 0, 0, 0, 0, 0]` also writes zeros to
the mode, override and `41132` positions. Whether the cloud forwards it
unchanged is not shown. For a single unit, "activate" first checks the
phone's schedule cache: if the cache is empty, it opens the schedule screen
instead. Because login empties the cache and parsing the empty value throws,
the button probably does nothing after a fresh login until the schedule
screen has been opened once; this is a static inference. Deactivation is
always sent. A multi-unit selection has separate activate (`1`) and
deactivate (`3`) buttons. Writes are grouped per gateway, the write status is
ignored, and only a failed request shows "Action not successful".
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

In detail, `42002` and `42005` have the range `1..100`, while `42001` and
`42004` have **no** range in the metadata, so the slider falls back to
`0..100`. All four are labelled m³/h. `42000` is in % without a step, `42003`
in ppm.

For direct-setting writes the app starts a cloud write and then polls live
data for an equal value. Other paths use asynchronous job status. Neither
mechanism establishes physical behaviour.

- **Read.** One `POST /device/data/get` for the six registers, then the usual
  status poll. `5030` re-issues the request once. If the job is still `105`
  after eight polls, or no `statusId` was returned, no error is shown and the
  spinner stays.
- **Write.** The value goes to every selected unit at once with `forEach`,
  **not** grouped per gateway. Confirmation polls `/device/data/live` up to
  eight times until the value loosely equals what was sent; a mismatch on the
  eighth poll or an HTTP error fails that unit. If the initial write request
  fails, the screen shows the read error text and never finishes the write
  for that unit.

### Tag read and write protocol

For a selected tag, **JS 786**:

1. writes the tag number to cloud register `40911`, waits for the write-status
   helper, requests `40901`, and polls that read's status for the value;
2. writes a changed tag to `40949` as the comma-separated four-value sequence
   `[tag, 0, rawValue, 0]`;
3. after the write-status helper, requests `40954` and accepts a single
   returned value of `2` as the tag-write status. It then displays the value
   it sent; this particular check is **not** a fresh read of `40901`.

Further details of the read:

- The selector write and the `40901` read are separate cloud jobs. The result
  of the selector write's status poll is ignored, and the `40901` read job is
  started even if the selector write failed.
- A completed read stores the returned `{address: 40901, value}` object.
  Nothing verifies that the value belongs to the selected tag; `105` keeps
  polling, any other status or eight polls without a result show "Unable to
  read device settings".

Further details of the write:

- **Raw value.** A slider value is sent as is; for a tag with `stepTag` the
  app sends `round(value / stepTag)`. Negative values are sent as negative
  decimals, without a 16-bit two's-complement conversion. On the reachable
  pages every minimum is at least 0 and only tags `81` and `82` have a
  `stepTag` (raw value = °C × 2); negative and 32-bit values occur only in the
  unreachable service UI.
- **Product filter.** A unit receives the write only if its own product's list
  for the current page contains the tag. On a mixed selection, M-WRG units
  silently skip `44`, `45` and `151..154`.
- **No selector.** The `40949` write does not touch `40911`; the meaning of
  its two zero fields is unknown.
- **Confirmation.** The `40954` check runs only if no unit in the same batch
  has failed yet and the screen is still open; otherwise later units are
  written without it. For one unit a failure shows "Unable to write device
  settings"; for several the app lists the units that did not respond.
- **Order.** Units on the same gateway are written in sequence, different
  gateways concurrently.
- **Stop flag.** Saving sets a flag that stops further tag reads; ordinary
  pages never reset it, so tags not read yet stay `- -` until the page is
  reopened.

The metadata's `index` is a separate catalog/display field, not the tag
number or a demonstrated local register (see
[Service catalog inventory](#service-catalog-inventory)). For a `stepTag`, the
UI sends `round(displayValue / stepTag)`; on read it multiplies the raw value
by that scale, with signed-16-bit interpretation where the minimum is
negative, and rounds the result to the step's decimals. Concurrency can race
the shared tag selector, and no local availability of these addresses
follows from the cloud protocol. The previously attempted local checks are
described in [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).

### What the app settings screen does

**JS 786** uses two distinct setting paths:

- The six direct settings in `settingRegisters` (`42000..42005`) are read as a
  register list. A changed value is sent to `/device/data/set` using that
  register address; the app then polls `/device/data/live` until the returned
  value equals what it sent. This is the app-side path corresponding to the
  documented humidity/CO2 threshold and airflow-bound registers.
- The other configurable items are catalog tags, grouped into pages such as
  Boost, Control Panel, Cross Air, Two Sensors, Interval, Temperature and
  Other. For a single device, the UI selects a tag with `40911`, reads its
  value through `40901`, and edits it through the `40949` payload described
  above. `SERVICE` mode uses the product's `allTags` catalog; ordinary setting
  pages use its selected tag list.

The tag UI converts displayed values using each tag's `min`, `max`, `step`,
`unit` and optional `stepTag`. On successful tag write, the app accepts
`40954 == 2` and updates its displayed cache to the value it sent; that is
not an independent readback of the persisted tag. These routes are cloud API
calls made with `deviceId`, not evidence that the same numeric addresses are
writable over local Modbus.

For example, tags `11..15` are saved configuration, while LOW/MED/HIGH runtime
selection uses the distinct `41120` mode codes shown above. Similarly, tag `12`
is the configured intensive duration, whereas activating intensive
ventilation uses the temporary `41120` override. The app also exposes more
complex service tags for humidity/CO2 control, two-sensor control, fan
calibration, frost protection and other functions. Their presence, labels,
defaults and ranges in the catalog do not establish safe local write
registers. This integration currently implements the documented local
`42000..42005` setting writes; it does not implement the app's service-tag
write protocol.

#### Settings page layout

The unit's menu offers, in this order: Settings, Time program, Interval,
Temperature program, "All tags" (only with `Settings.SERVICE`) and Cancel.
The same menu appears for a multi-unit selection. The Settings page shows
these sections; labels are the app's English / German UI strings:

| Section | Items and labels |
| --- | --- |
| RH control / Feuchte-Regelung | `42000` start value, `42001` / `42002` minimum / maximum airflow |
| CO2 control / CO2-Regelung | `42003` start value, `42004` / `42005` minimum / maximum airflow |
| Boost / Intensivlüftung | `11` airflow, `12` run-on time |
| Control panel / Bedienfolie | `13..15` "Button 1..3 (I..III) LOW / MED / HIGH"; M-WRG-II also `44` / `45` "Button 4 (IIII) exhaust / supply" (`Taste 4 (IIII) Abluft / Zuluft`) |
| Cross air / Querlüftung | `43` / `42` `Zuluftbetrieb` supply / extract, `47` / `46` `Abluftbetrieb` supply / extract |
| CO2 two sensor / CO2-Regelung mit VOC – Zuluft | `151` enable, `152` VOC start value, `153` minimum supply airflow, `154` delay; hidden when the product's list is empty (M-WRG) |
| Other / Sonstige | `7` standby pause, `8` standby sample time, `9` standby airflow, `10` close flaps, `76` buzzer (`Akust. Signale`) |

The Interval page shows `67` pause airflow, `66` operating airflow, `69`
pause time and `68` operating time; the Temperature page shows `65` airflow
and `81` / `82` minimum / maximum supply temperature. No page hides a section
because of the humidity or CO2 sentinels or the VOC sensor: the app does not
enforce the manufacturer's statement that the temperature program needs a
humidity sensor.

#### Reads triggered by opening a page

For a single unit, opening a non-service page immediately reads every tag of
that page one after another, each with its own `40911` selector write and
`40901` read:

| Page | Tags in read order |
| --- | --- |
| Settings, M-WRG-II | `11, 12, 13, 14, 15, 44, 45, 43, 42, 47, 46, 151, 152, 153, 154, 7, 8, 9, 10, 76` |
| Settings, M-WRG | `11, 12, 13, 14, 15, 43, 42, 47, 46, 7, 8, 9, 10, 76` |
| Interval | `67, 66, 69, 68` |
| Temperature | `65, 81, 82` |

The Settings page reads `42001..42005, 42000` in parallel. Merely opening the
page therefore causes 20 selector writes on an M-WRG-II unit. The page ends
with tags `9` (default 20), `10` (default 1) and `76` (default 1), and a save
does not cancel a tag read already in progress. This could explain the local
`40901` values `1` and `20` that did not match the setting just changed in
[SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md#2026-10-16--local-keypad-readback-on-slave-4);
the link is a static hypothesis that needs a capture.

A multi-unit selection reads nothing. It always uses the M-WRG-II catalog and
shows catalog defaults: `60/10/60/600/10/60` for the six registers and
`default / stepTag` for tags.

#### Boolean tags

Only tags `10`, `76` and `151` are reachable boolean settings. A boolean is a
toggle: the app writes `0` if the current value is truthy and `1` otherwise.
An unread value shows "OFF", but the dialog acts on the tag's default, which
is `1` for all three tags, so pressing it writes `0`. Tag `93` uses the label
`RESET` for both states.

#### Interval and temperature pages

Each page ends with a Start button that sends `41120`
`INTERVAL [3, 231, 0 ×11]` or `TEMPERATURE [2, 224, 0 ×11]`. That write goes
to all selected units at once, without confirmation, and the page closes
immediately; the tag values on the page are saved only through each tag's own
Set button.

#### Unreachable setting paths

- **Night ventilation.** No screen navigates to the night list, no code sends
  the `NIGHT` mode array, and the app has no labels for tags `58..61`. Night
  appears only as an active-mode banner (German: `Sommer Nacht Programm`).
- **Tags `19`, `33` and `93`** are in the ordinary table but in no page list.
  The special write rule for tag `19` (a written `1` becomes `2`) and the
  navigation back after writing tag `93` therefore never run.
- **Service UI.** Nothing sets `Settings.SERVICE`. If it were set, the
  "All tags" page would show tag numbers with English internal names, a
  search box, a per-tag refresh and a numeric input; a `max` of `0` would fall
  back to 100, which affects the read-only tags `83` and `84`.

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
| 42 / 43 | Supply-only (`Zuluftbetrieb`): extract / supply | m³/h | 0..100 | — | 0 / 50 |
| 44 / 45 | Key 4 extract / supply; the internal name of `45` says key 5 | m³/h | 0..100 | — | 70 / 70 |
| 46 / 47 | Extract-only (`Abluftbetrieb`): extract / supply | m³/h | 0..100 | — | 50 / 0 |
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

- Only tag `7` differs in its minimum: the ordinary table gives `5..255`, the
  service table `1..255`. Both app tables give tag `8` a minimum of 1. The
  manufacturer gives `7 = 1..255` and `8 = 5..255`, and the app manual says
  the sniffing time is at least 5 minutes; neither app table has that
  minimum for tag `8`.
- The ordinary HIGH default is 60; the shared service entry says 50.
- The ordinary two-sensor threshold `152` defaults to 1000 with a step of 10;
  the service entry says 1500 with a step of 1.
- Tag `154` is labelled a **delay in minutes** in both app tables, not a
  maximum airflow. Tag `76` is a **buzzer** in both tables, not a cross-air
  enable switch. These are app-label findings, not new hardware measurements.
- The extracted `46` / `47` labels are extract / supply. Any differing
  interpretation in another implementation requires isolated verification.
- The internal name of `45` says key 5, but the settings page shows the
  localized label "Button 4 (IIII) supply" / `Taste 4 (IIII) Zuluft`, which
  matches the manual. The internal name is not displayed outside service mode.

The ordinary and service entries also differ in these fields:

| Tag | Ordinary table | Service table |
| --- | --- | --- |
| `42` | default 0 | default 10 |
| `19` | max 2, unit `bool` | max 3, no unit |
| `33` | max 255, "Available Internal Sensors" | max 65535, "Available Sensors" |
| `68`, `69` | step 10 | step 1 |
| `58` | step 10 | step 1 (M-WRG-II product entry) |
| `61` | default 10, m³/h | default 30, % (M-WRG-II product entry) |
| `9`, `11`, `13..15`, `42..47`, `60`, `61`, `65..67`, `153` | m³/h | % |
| `44`, `45` | "Taste 4/5 ( WRG-II ) ABL/ZUL" | "CrossAir IN Exhaust/Supply high" |

## Service catalog inventory

**JS 696** contains 155 shared service metadata entries plus
product-specific additions for tags `58..61`, which the shared table does not
contain. The products' `allTags` catalogs therefore have 158 (M-WRG-II:
`58`, `60`, `61` added) and 159 entries (M-WRG: `58..61` added). The
M-WRG-II service list selects 149 tags and the M-WRG list selects 104. A
table entry existing does not mean it is selected for both series. In
particular shared tags `35` and `197` are not in either product's service
list. These parameters may control safety, calibration or factory state:
**do not write them based on this catalog**.

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

The product-specific night entries disagree. M-WRG-II
describes `58` as runtime, `60` as supply and `61` as extract, defaults
360 min / 10% / 30%. M-WRG describes `58` as start time, `59` as duration,
and `60` / `61` as supply / extract, defaults 1320 min / 480 min / 50% / 0%.
The ordinary tag table has yet another `61` default of 10. These are distinct
metadata contexts, not established device defaults. Read as minutes after
midnight, the M-WRG start time `1320` is 22:00 and the duration `480` is 8 h.

The `index` field looks like the service menu's ordinal for M-WRG-II: that
product's service list is sorted by `index` and covers exactly `1..150`
except `44`, which belongs to the excluded tag `35` ("Disable RF"); index
`150` is tag `60110` ("Store NVM"). All entries with index `0` are
M-WRG-only. Index `9` is used by both `7` and `197`, and the ordinary table
gives `151..154` the indices `114..117` while the service table uses
`115..118`. No code reads the field, so this meaning is inferred.

### Service metadata compared with other sources

The catalog differs from the manufacturer's remote-control parameter table
([reference/device-parameters.md](reference/device-parameters.md)) in several
values. The comparison is between two documents; it does not show which value
a unit uses.

| Tag | App | Manufacturer |
| --- | --- | --- |
| `7` | Ordinary minimum 5, service minimum 1 | 1..255 |
| `8` | Minimum 1 in both tables | 5..255 |
| `38`, `41` | Minimum 1 | Minimum 0 |
| `39` | Step 1, default 600 | M-WRG-II step 10, default 800; M-WRG step 1, default 600 |
| `101` | `0..2`, default 2 | M-WRG-II `0..2`, M-WRG `0..3`, default 1 |
| `119` | Default 8 | Default 11; mode 8 means "not used" |
| `196` | Default 180 min | Default 120 min |
| `15` | Ordinary 60, service 50 | M-WRG 60, M-WRG-II 50 |
| `42` | Ordinary 0, service 10 | M-WRG-II 0, M-WRG 10 |
| `96` | "Gira OFF button enable" | InControl standby on/off |
| `151..154` | Two-sensor CO2/VOC control | Reserved |

The M-WRG service list includes `44` and `45`, which the manufacturer marks
as M-WRG-II only, and omits `168..170`, which it lists for both series.

Several service defaults match manufacturer function descriptions in
[reference/functions.md](reference/functions.md) and
[reference/maintenance.md](reference/maintenance.md). This is a
correspondence of numbers only:

- Frost protection: tag `2` start temperature −1.5 °C, tag `6` measurement
  period 180 s, tags `160` / `161` 2 °C and 120 min for the fan-speed check,
  and tag `3` stop temperature 4 °C, which is the documented return value of
  the older series rather than the M-WRG-II's 5.5 °C.
- Filter: tag `24` lifetime 8760 h (one year), tag `26` buzzer time 1 s, and
  tags `28..32` buzzer intervals 12, 9, 6, 3 and 1, which fit an interval that
  shortens to hourly.
- Minimum ventilation: tags `126..129` give 08:00–20:00 at 40 and 20:00–08:00
  at 20.

Two register groups mirror service tags. `42000..42005` have the same defaults
as tags `36..41` and equivalent ranges (`42001` and `42004` have no explicit
range, and the slider fallback `0..100` equals tags `37` and `40`), but the
airflow registers `42001`, `42002`, `42004` and `42005` use m³/h and step 10
where the tags use % and step 1, and `42003` uses step 10 where tag `39` uses
step 1. `42006` "Priority Override Mode" has the same name as
tag `95`, and the tags `95`, `54`, `55` and `56` have the defaults `12`, `60`,
`1` and `15`. These are exactly the values the 2026-11-03 survey read from
local `42006..42009` on all six units
([MELTEM.md](MELTEM.md#app-derived-register-survey-on-2026-11-03)). The
remote-control mapping in device-parameters.md already pairs `54..56` with
`42007..42009`; the app adds `95` with `42006`.

A few exact app names that the family table above paraphrases: `51..53`
"BCD choise LS05/LS07/LS09" (sic), `96` "Gira OFF button enable", `124` /
`125` "PIR Ventilation level" / "PIR runOnTime", `197` "Frostprotection
Disable closing Supply Valve", `34` "BootMode", `35` "Disable RF", `92`
"set Unixtime" and `60110` "Store NVM".

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

The product-specific additions are not part of that shared table:

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

- The normal schedule screen reads through
  `fetchTimePrograms -> getDeviceTimePrograms -> GET /device/timeprogram/get`.
  The response's `switchPoints` array is the source for the screen; the app
  fills missing `49015..49017` properties with zero, assigns an ID from
  day/hour/minute, sorts the list, and caches it on the phone.
- A switch point has `dayOfWeek`, `hour`, `minute`, and `registers` containing
  `49015` mode, `49016` submode and `49017` second submode. The editor creates
  an ID from day/hour/minute and sorts by those fields.
- Day numbers are **0 Monday, 1 Tuesday, 2 Wednesday, 3 Thursday, 4 Friday,
  5 Saturday, 6 Sunday**. The editor converts JavaScript's Sunday-zero
  `Date.getDay()` using `(day + 6) % 7`, labels those values Monday-first,
  and passes the chosen number directly as `dayOfWeek` when building the
  JSON sent by `device/timeprogram/set`. No conversion is visible in that
  request-building path.
- The add dialog rejects a save when the cached list plus the selected days
  would exceed 42 switch points, and rejects a point whose day, hour and
  minute already exist on any selected device. The manufacturer manual states
  up to six per day and 42 per unit; the client checks **no** per-day limit,
  and using a template skips both checks.
- The normal editor gets the existing schedule, modifies a cached copy, sorts
  it and sends the **whole** resulting list as the JSON body to
  `POST /device/timeprogram/set`; the query also carries `deviceId` and
  session parameters. The editor polls `/device/timeprogram/set/status` for
  completion (`6000`), then reloads. Delete-all sends an empty list. Templates
  and a per-device cache live on the phone; templates are intentionally
  retained by the app's logout cleanup.
- A separate helper in **JS 788** selects an index by writing `49011`, writes
  an action to `49000` (`READ=0`, `EDIT=1`, `DELETE=2`, `DELETEALL=3`,
  `ADD=4`), then polls live values for `49001`, `49002`, and `49011..49017`.
  Its `handleRfProblem` routine searches selected indices and can add a point
  when it does not find the requested data. Static reference search found no
  in-bundle caller for either `actionTimeProgram` or `handleRfProblem`; this
  helper therefore is not the normal editor read/write path in this bundle.
  Its presence is evidence of an alternate/legacy API workflow, not proof of
  local serial access or a safe sequence to use from this integration.
  Details are under [Legacy `490xx` helper](#legacy-490xx-helper).
- App schedule airflow editing can encode a positive level as
  `round(value / 10) + 200`; mode `4` uses separate sides. This encoding and
  the local integration's raw airflow scaling are different contexts.

### Schedule editor details

The JSON body of `POST /device/timeprogram/set` is a bare array of objects:

```json
[{"id": "<day>_<hour>_<minute>", "dayOfWeek": 1, "hour": 12, "minute": 5,
  "registers": {"49015": 3, "49016": 229, "49017": 0}}]
```

The list contains the cached server objects, including the client-added `id`
and any other server fields, plus the new points, sorted by day, hour and
minute. Hour and minute are numbers; the time picker has no minute interval,
so the resolution is one minute. The editor can produce these values:

| Choice | `49015` | `49016` | `49017` | Notes |
| --- | ---: | --- | --- | --- |
| LOW / MED / HIGH | 3 | 228 / 229 / 230 | 0 | LOW is the default for a new point. |
| AUTO | 2 | 16 | 0 | Disabled if the humidity **or** CO2 sentinel is present. |
| CO2 | 2 | 144 | 0 | Disabled with the CO2 sentinel. |
| RH | 2 | 112 | 0 | Disabled with the humidity sentinel. |
| Standby (OFF) | 1 | 0 | 0 | |
| MANUAL | 3 | `v / 10 + 200` (201..210) | 0 | `v` from 10 to 100 in steps of 10. |
| Extract or supply button | 4 | supply: 0 or `round(v / 10) + 200` | extract: 0 or `round(v / 10) + 200` | Both zero is blocked. |

BOOST, night, interval and temperature are never offered. For display,
`getSwitchPointMode` returns the supply icon for mode `4` when
`49016 > 49017` and the extract icon otherwise, including equal values; other
points are matched against the mode table in key order and ignore `49017`.
Airflow is shown as `10 × (code − 200)`; unknown combinations show no text.
Only the MANUAL label is translated.

Further editor behavior:

- **Single unit only.** The switch-point list is shown only for one unit. A
  multi-unit selection can add points, apply templates and remove all, but
  cannot edit or delete single points.
- **Edit.** Editing removes the old point by `id` and appends the new one. If
  a unit's cache lacks that `id`, nothing is added and the unchanged list is
  still sent. Remove in the edit dialog deletes without further checks.
- **Read.** The list is read once per screen; the retry wrapper's check
  always succeeds, and an error shows a full-screen message without retry. A
  cache-first path exists but has no caller.
- **Save rounds.** Units are processed strictly one after another, not grouped
  per gateway. A failed unit is retried in the next round;
  `MAX_TIME_PROGRAM_SAVE_LOOPS = 3` means three rounds in total. Afterwards
  the list is re-read, and failed units are named in the editor.
- **Templates** are stored in `MELTEM_TIME_PROGRAMS_V112` as a JSON array of
  `{name, data}`, where `data` holds the cached switch points including `id`.
  Saving needs a non-empty list and a unique name, offers to replace a
  duplicate, and only renames on edit. Applying a template sends its sorted
  `data` as the complete list to every selected unit, without the 42-point,
  duplicate, sensor or product checks.

### Legacy `490xx` helper

`actionTimeProgram` (**JS 788**) writes `49011` first: for `READ` just the
index, for `ADD` the seven-value tuple
`[index, day, hour, minute, 49015, 49016, 49017]`, i.e. `49011..49017` in
order. It then writes the action code to `49000`, ignores both write
statuses, and polls `/device/data/live` up to eight times for `49001`,
`49002` and `49011..49017`. The verifier requires nine values and
`49001 == 3` ("finished"); add, edit and delete also require `49002 == 0`. A
slot counts as empty when `49012..49017` are all zero.

`handleRfProblem` walks a list of `{previous, next, switchpoint}` items. For
each item it reads the slots after `previous` up to `next`, shifted by the
number of points it has already added, and stops at an empty slot. If a slot
holds exactly the wanted tuple with `49002 == 0`, it reports it as found;
otherwise it **adds** the tuple at the slot it stopped at, even after a read
error. Only `READ` and `ADD` are used, and only inside this uncalled routine.
Two further verifiers in **JS 789** and **JS 799**, also uncalled, describe an
older index-based protocol: add and edit succeed when `49012..49017` echo the
sent values and `49011` reports the insert position; delete succeeds on
`[index, 0, 0, 0, 0, 0, 0]`.

The local observations `49000..49002 = [4, 3, 0]` after app saves and
`[3, 3, 0]` after deleting all points
([MELTEM.md](MELTEM.md#app-time-program-comparison-on-2026-11-03)) match the
codes `ADD = 4` and `DELETEALL = 3` and the helper's "finished" and "OK"
values, and the observed `49011..49017` tuples match its tuple order. This
suggests that the cloud or gateway drives the same register protocol when it
applies a `switchPoints` list. Nothing in the app shows that, and none of it
is a safe local write sequence.

### Schedule observations on hardware

The static app path and the controlled `49012=2` read for a Wednesday point
are consistent with Monday-zero for this app and device. Three additional
rounds across all six units found `41106=0` on units 2–4 and `3` on units 5–7;
the user reported that the latter programs were not active. The values and the
unit-5 schedule-register responses are recorded in
[MELTEM.md](MELTEM.md#cross-unit-scheduler-state-on-2026-11-03). The sample
supports, but does not prove, that `3` means a stored, inactive program.
The later two-point execution test below confirmed Tuesday scheduling on
slave `4`; behavior on other weekdays and firmware remains untested.

A subsequent app-only two-point test on unit `4` changed the second point
from LOW to MEDIUM, but the readable `49011..49017` tuple continued to show
the first HIGH point. The user removed both test entries afterwards. This
shows that reading the register window with its existing selector value of
zero did not return the second point. The app's normal editor gets its list
from the cloud `switchPoints` endpoint; the separate selector/action helper
was not invoked in this test and has no in-bundle caller. The result therefore
does not establish whether other schedule points live at other local
registers, behind selector values, or only in the cloud-backed API. Details
are recorded in
[MELTEM.md](MELTEM.md#two-point-app-schedule-on-2026-11-03).

A later app-only execution test set Tuesday MED at `12:05` and HIGH at `12:10`
on slave `4`. The measured airflow changed to `30/30` and then returned to
`60/60`, confirming that the cloud schedule actually executes on this unit.
During MED, the readable `49011..49017` tuple still described the second HIGH
point. This confirms neither a complete local schedule list nor a safe local
schedule write protocol; see
[MELTEM.md](MELTEM.md#two-point-schedule-execution-on-2026-11-03).

## Languages, links and libraries

- **Languages.** **JS 686** contains only English and German. Without an
  account language the app follows the phone locale (exact match, then the
  language prefix) and otherwise falls back to English, the first entry. After
  login it switches to the account's `language` from `/user/details`. The
  German table lacks four keys for the "remove all" schedule dialogs.
- **Mode names.** There are no translated names for the runtime modes except
  MANUAL (`MANUELL`); schedule lists show raw keys such as `LOW` or
  `OM_CROSS_AIR_SMT_IN`. Night ventilation is called `Sommer Nacht Programm`
  in German. Unused strings include humidity and CO2 limit warnings
  ("Not healthy - More ventilation preferred"); the app has no humidity or
  CO2 alarm thresholds.
- **Links.** FAQ, privacy, imprint, service and contact pages on
  `www.meltem.com` (separate English and German URLs) and one filter-shop link
  on `shop.meltem.com`. The contact screen offers filter order, service,
  contact and FAQ.
- **Account rules.** Registration filters the username to
  `[a-zA-Z0-9-_.+@]`. A password needs at least 8 characters including one
  letter; the stricter rule (lower case, upper case, digit, special character)
  only colours the field. After a password change the user is told that other
  phones will be logged out.
- **Libraries.** The bundled licence list (**JS 837**) has 30 entries without
  version numbers: the four `@react-native-community` modules (async-storage,
  datetimepicker, netinfo, slider), `email-validator`, `lodash`, `long`,
  `prop-types`, `react`, `react-native` and thirteen `react-native-*` modules
  (including `camera`, `udp` and `localization`), four `react-navigation`
  packages, `react-redux`, `redux` and `redux-thunk`. Only `long` (Apache-2.0)
  and `react-native-camera` (MIT, Apache-2.0 and BSD-3-Clause) are not plain
  MIT. The same list is also present as `res/raw/licenses.json`.

## Unused and unreachable code

The following parts of 1.17 exist but are not reachable from the user
interface. They describe what the bundle contains, not what the cloud or the
units support.

| Item | Status |
| --- | --- |
| `bridge/info`, zone and `data/set/status` wrappers, `bindProduct` without serial | No caller; zone wrappers call non-existent helpers. |
| `actionTimeProgram`, `handleRfProblem` and their helpers; two `verifySwitchPointData` functions; `timeProgramValid` | No caller. |
| `41113` decoding | Runs, but its result is discarded. |
| `41133` cross-air branch | Unreachable; only MANUAL uses `41133`. |
| Night settings page and `NIGHT` mode array | Never navigated to or sent. |
| Tag `19` write mapping and tag `93` navigation | Tags are in no page list. |
| Service ("All tags") UI | `Settings.SERVICE` is never set. |
| Error-register rendering in the overview | Never called. |
| Schedule cache-first path | No caller. |
| `ProductUnbindWait` route in the home stack, `Terms` screen, drawer menu, `Footer`, `Menu` and redirect components | Never navigated to or used. |
| Gateway URL state display, `discoverModbusStop`, `MODBUS_CONNECTION` listener | Not displayed, not implemented natively, never emitted. |
| Native `UDPClient` and `ModbusProtocol` | Not reachable from the exposed native methods. |
| Constants `BRIDGE_STATUS_INTERVAL`, `MESSAGE_VIEW_INTERVAL`, `IOS_ALERT_DELAY`, `SET_REGISTER_SLEEP`, `RF_TIME_PROGRAM_WORKAROUND` | Never read. |

The device-list reducer also starts from a malformed initial state: it spreads
the action-type string `"INIT_ERRORS"` into the state object instead of the
error fields. This has no visible effect once the first error check has run.

## Implications and open questions

- Do not add local reads or writes for `409xx`, service tags, `41133`,
  `42006..42009`, or gateway setup registers simply because they occur in
  the app. Cloud register names, gateway addresses, tag numbers and
  room-unit holding registers are different namespaces.
- Never read gateway `41030..41098`: the app writes a credential and the cloud
  binding there. Raw register diagnostics
  ([TODO-5](TODO.md#todo-5--raw-register-diagnostics)) must exclude that range
  on the gateway.
- The app's 13-word cloud control request and local serial writes have
  different transport and confirmation semantics. Existing component
  register ranges and request timing remain unchanged.
- One controlled Tuesday schedule executed at the expected times on slave `4`;
  validate other weekdays and firmware before generalizing that behavior.
  The execution result does not establish a local schedule write protocol.
- Do not map app service tags `76`, `154`, `46`, or `47` to local Modbus writes:
  the APK metadata alone does not establish a local register mapping or safe
  write sequence.
- App metadata for product, sensors, ranges, defaults, VOC units and service
  options is not sufficient to infer an exact device profile or capability.
  Continue to distinguish absent, invalid, stale and unreadable values.
- The integration already decodes the app's sensor sub-modes `16/48/80`,
  `112` and `144/176/208` and uses the same absolute-humidity formula; the
  re-analysis requires no code change.
- Read-only hardware follow-ups: the `41113` mode word
  ([HW-12](HARDWARE_BACKLOG.md#hw-12--legacy-mode-word-41113)), the
  temporary override registers `41103..41105` for intensive detection
  ([HW-13](HARDWARE_BACKLOG.md#hw-13--temporary-override-status-4110341105)),
  `40101` for detecting a silent unit
  ([HW-14](HARDWARE_BACKLOG.md#hw-14--detecting-a-silent-unit-through-40101)),
  the app program codes and `41106`
  ([TODO-8](TODO.md#todo-8--name-the-app-programs-and-the-time-program-state)),
  and the settings-page selector hypothesis for `40901`
  ([H-12](LIVE_GATEWAY_TESTS.md#h-12--app-settings-page-and-40901)).
- Changes already made from this analysis: the tools in `tools/` refuse reads
  of gateway `41030..41098`, and the optional `40103` diagnostic is named
  **Device fault**, as in the app.
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

The second pass needed no third-party tooling. The TypeScript compiler's
parser and printer (shipped with VS Code) can split the bundle at each `__d(`
line and print it with multi-line objects and blocks; the first-party
modules are roughly `678..851` plus the entry module `0`. A short script that
reads the DEX header tables can list each `com.meltem` class's fields,
string constants, invoked methods and `fill-array-data` payloads, which is
enough to check the native setup module without decompiling it. To find dead
code, search the whole formatted bundle for every exported name; a name that
appears only at its definition has no caller.
