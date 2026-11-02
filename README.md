# GateKeeper Pi server - app only + local QR enrollment

The working GateKeeper tablet backend, separated from the operator web dashboard.
The Flutter tablet app is a separate project.

## What is included

- Original reference tablet login, gate ownership/leases, PLC FC1/FC15 polling,
  Key/Energize, Open/Close, watchdogs, session cleanup and auditing.
- SQLite application users, settings, zones, registered gates and audit history.
- Local-monitor, five-minute, single-use QR enrollment with the site's public CA
  embedded in the QR and permanent per-tablet MQTT identities.
- Short `gatekeeper-enroll` command and optional sudo-authorized SSH initiation,
  displaying only on the physical Pi monitor; SSH mode is disabled by default.
- Mosquitto TLS/Dynamic Security, mapped Pi Ethernet telemetry/discovery, and
  bounded privileged network/management helpers.
- Optional authenticated HTTPS Node-RED maintenance editor on localhost only.
  **No operator dashboard or RevPi hardware I/O nodes.**
- A fresh-Pi installation entry point and automated tests.

## Start here

1. [Install on a NEW Pi](docs/INSTALL.md).
2. [Add the FIRST tablet using a LOCAL HDMI monitor](docs/FIRST-TABLET.md).
3. [What was done, verification and remaining acceptance](docs/HANDOVER.md).
4. [Pull changes, amend flows, maintain and back up](docs/MAINTENANCE.md).

For the private repository, complete the [GitHub authentication step](docs/INSTALL.md#2-clone-and-review)
on the Pi before cloning.

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/atworkshop55-hash/Gatekeeper-server-pi-apponlyQR.git
cd Gatekeeper-server-pi-apponlyQR
bash install.sh --plan
# NEW Pi only; use this Pi's actual unique hostname:
sudo bash install.sh --host gatekeeper-nexus.local
```

Requires **Raspberry Pi OS Lite Trixie (Debian 13), 64-bit, Pi 4/5**, an HDMI
monitor/keyboard, and **two distinct physical Ethernet adapters**. Choose a unique
hostname in Raspberry Pi Imager. The installer refuses an existing or partial
GateKeeper installation and excludes RevPi hardware.

Installation generates new keys, TLS identities and an empty database locally.
There are no site credentials, private keys, tablet accounts or database backups
in this repository. Do not copy another site's state to create a new server.

**Status:** the app-only system is working on the test Pi and was reported working
by the operator. Standalone source passed isolated control, admin/discovery,
SQLite and TLS QR tests. The newly assembled end-to-end fresh-install command
still needs a clean-SD-card installation and physical enrollment acceptance;
do not confuse isolated tests or the successful existing-system upgrade with
that remaining test. See [the handover](docs/HANDOVER.md).

PLC writes are included. Installation leaves the backend stopped and creates no
demo gates. Keep motor/actuator power isolated until deliberate commissioning.

## Tests

On Linux:

```sh
python3 -B -m unittest discover -s test -p '*_test.py'
node --test test/reference-platform.test.js
```

With the installed Pi's Node.js/Mosquitto/QR dependencies available, the following
use private temporary data, brokers, sockets and a loopback PLC simulator only:

```sh
python3 -B test/installer_reference_app_integration.py
python3 -B test/enrollment_integration.py
```

Do not run the fresh installer on the working system just to test it.
