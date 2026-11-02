# Handover - 2 November 2026

## Goal and result

Separate the working tablet/server side from the large operator web flow.
Keep the reference RevPi unchanged. Run the original backend control behavior
on a Raspberry Pi with local-monitor QR enrollment, then store that server in
its own repository with installation and maintenance instructions.

The source reference was read from the RevPi on 1 November. All 474 original
nodes matched the canonical reference export at that time. The selected
app-only extraction removes the operator dashboard, HTTP dashboard endpoints,
disabled history and RevPi-specific hardware nodes.

## Working test installation

The preserving upgrade on the existing Pi succeeded on 1 November, retaining
the existing SQLite database, TLS identity, enrolled tablet accounts and
localhost HTTPS maintenance editor. The operator reported it working nicely.
The reference RevPi was not changed.

The original control handlers and raw TCP transport were retained, including
login/session ownership, leases, Key/Energize, Open/Close, heartbeat cleanup,
watchdogs and FC1 feedback/FC15 coil writes. No replacement commissioning
firewall or fabricated physical-stop signal was introduced. The reference
parser's V1.1 label is not a new independent physical-stop guarantee.

Platform-only adaptations:

- Mapped management/gate Ethernet roles by physical MAC and driver.
- Pi telemetry instead of the RevPi hardware sampler.
- `ip neighbour` output adapted to the original discovery parser.
- A peer-restricted local Unix socket to fixed root-owned network helpers,
  rather than blanket backend sudo.
- Management pending-change confirmation/recovery monitoring.
- Explicit failure for switch power-cycle, since no Pi power output is mapped.
- SQLite/log paths under the Pi runtime directory.
- Existing local HDMI QR generation with embedded public CA, single-use
  bootstrap redemption and permanent per-device MQTT accounts.

## This repository

Only the selected server flow and required setup code were brought over.
The operator web dashboard, Flutter app, unrelated historical experiments,
experimental commissioned-control profile and demo gate sampler are excluded.
Some source/helper names still say `revpi` because the same validated network
logic was reused; this does not install vendor RevPi packages or control I/O.

The standalone fresh-install entry point composes the prior foundation,
TLS/database, app-admin, Ethernet and maintenance-editor stages, then installs
the selected server flow. It generates site secrets on the target, records
source hashes, refuses existing installations, and leaves the backend stopped
for deliberate activation. This differs from the earlier preserving-upgrade
package used on the already-configured test Pi.

## Verification

Standalone tests cover database schema/defaults, first administrator,
HDMI setup, QR renderer geometry/parsing, Ethernet identity validation,
network helper validation/rollback, management recovery, host substitution,
flow graph/credential exclusions and original control preservation.

The actual selected Node-RED flow was also exercised with:

- A private MQTT broker, SQLite database and loopback PLC: login, ownership,
  Key/Energize at `0xB8`, Open feedback, release/heartbeat loss, audit records,
  discovered-gate registration and network-result persistence.
- An isolated TLS/Dynamic Security broker plus local QR socket: embedded CA,
  broker hostname, readable full-size QR matrix, one-use redemption, permanent
  device application login and bootstrap cleanup.

These fixtures never write to the real PLC, reconfigure live Ethernet or touch
the working site's database. Running the test suite is not commissioning.

## Outstanding / deliberately not claimed

- **A full fresh-SD-card run of the new one-command installer**, then physical
  HDMI scanning, restart/late-monitor and tablet/PLC acceptance on that new Pi.
- Automatic partial-install resume or in-place upgrades: intentionally absent.
- Universal Raspberry Pi images, desktop/32-bit images or deployment onto a
  working RevPi: not supported by this installer.
- A mapped physical switch power-cycle output on the Pi: not implemented.
- Application password hashing migration: the original compatible password
  storage remains in protected SQLite; do not expose/copy the database.
- Exhaustive fault, cancellation, forced power-loss and real actuator acceptance
  for all reference behavior: not implied by the successful lab operation.

The next recommended test is a separate clean card/Pi, **not reinstalling the
working server**. Keep the existing system available as the operational reference.
