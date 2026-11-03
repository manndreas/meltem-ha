# RTU-over-TCP implementation plan

## Problem and approach

The integration currently assumes a serial USB device path throughout setup,
runtime connection creation, reconfiguration, and diagnostics. Add a generic raw
RTU-over-TCP option using Home Assistant's shared Modbus connection, while
preserving existing serial entries and keeping the register, entity, and
coordinator behavior transport-neutral.

## Agreed scope

- Support raw RTU-over-TCP only; do not add Modbus TCP/MBAP or UDP.
- Manually enter an IP address or hostname and TCP port, defaulting to `5020`.
- Do not add mDNS discovery in this implementation; keep it as the separate
  follow-up `TODO-7`.
- Allow Reconfigure to switch between serial and RTU-over-TCP.
- Identify network endpoints by normalized host and port. Preserve the existing
  config-entry ID and entity identities when Reconfigure changes the endpoint.
- Document that the ESP bridge has no TLS or authentication and is intended
  only for a trusted local network.
- Do not claim hardware validation: no ESP32 or Meltem gateway was available
  for this implementation. The live check is
  [HW-11](../HARDWARE_BACKLOG.md#hw-11--rtu-over-tcp-through-a-real-bridge).

## Work items

1. **Generalize connection configuration.** Add a typed representation and
   parameter builder for serial and RTU-over-TCP. Existing entries without a
   transport field must continue to mean serial. Build network parameters with
   the verified `socket://` RTU framing path and create a normalized endpoint
   identity.
2. **Extend setup and Reconfigure.** Add manual network host/port entry,
   validation, gateway discovery, and the ability to switch transport while
   retaining USB discovery and the existing profile workflow. Preserve unit
   subentries and entity IDs.
3. **Wire runtime and diagnostics.** Use the selected connection parameters
   when creating the shared Modbus client and temporary setup connections.
   Generalize endpoint-aware logs, error text, diagnostics redaction, and
   System Health without exposing the configured host.
4. **Add regression coverage and update documentation.** Keep the local
   RTU-over-TCP loopback spike as a test, including CRC checks, the bridge's
   40 ms idle-frame gap, sequential requests on one TCP connection, and the
   existing request pacing. Cover setup, switching transports, legacy serial
   behavior, and diagnostics redaction. Update user-facing setup documentation
   and document the connection and security requirements.

## Notes and validation

- The local spike passed with Home Assistant `2026.11.0b0`,
  `modbus-connection 4.12.7`, and `tmodbus 1.1.0`: three RTU reads succeeded
  over one TCP connection using the integration's `prepare_unit`.
- The initial transport spike used a local test server to exercise RTU frames
  over a TCP connection, not the ESP firmware or physical Meltem hardware.
  The implementation has been tested against the same kind of software
  servers and the [local mock gateway](../MOCK_GATEWAY.md) only.
- Validate each implementation stage with focused tests. Finish with the
  relevant Ruff, mypy/Pyright, and integration tests; do not change release
  metadata unless a release is requested.
