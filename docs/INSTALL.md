# Install on a new Raspberry Pi

## 1. Prepare hardware and operating system

Use Raspberry Pi 4 or 5 with Raspberry Pi OS **Lite Trixie 64-bit (Debian 13,
arm64)**. Desktop images, Bookworm, 32-bit images, and RevPi targets are not
supported by this installer.

In Raspberry Pi Imager:

1. Select the Lite 64-bit Trixie image.
2. Set a unique hostname, for example `gatekeeper-nexus` or `gatekeeper-north`.
   Do not have two servers advertising the same `.local` name.
3. Create your own Linux maintenance username/password and enable SSH if needed.
   There is no assumed `pi` account or default password.
4. Configure the management LAN/Wi-Fi as needed. This installer does not set up
   an access point or the site's router.
5. Write the card, boot, and log in. Set the correct date/time before TLS setup.

Connect:

- The Pi's built-in Ethernet to the management/uplink network.
- A second physical Ethernet adapter (e.g. USB Ethernet) to an **isolated gate
  switch**. Both adapters are required, even if Wi-Fi provides internet.
- The PLC to the isolated switch, with logic power available but actuator/motor
  power isolated for initial testing.
- HDMI monitor and keyboard. Use the Pi's HDMI-A-1 output, or select HDMI-A-2
  explicitly in the command below.

Verify internet and management access before starting. NetworkManager must be
active. Avoid installing Node-RED, Mosquitto or creating a `gatekeeper` account
before this script: it intentionally requires a fresh system.

## 2. Clone and review

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/atworkshop55-hash/Gatekeeper-server-pi-apponlyQR.git
cd Gatekeeper-server-pi-apponlyQR
bash install.sh --plan
hostname
ip -br address
systemctl is-active NetworkManager
```

For a private repository, authenticate with your own GitHub credential/SSH key.
Never put an access token in the clone URL or commit it into this project.

## 3. Run the fresh installer

For a Pi whose hostname is `gatekeeper-nexus`:

```sh
sudo bash install.sh --host gatekeeper-nexus.local
```

Use the real hostname plus `.local`; the installer checks that they match.
The default without `--host` is this Pi's hostname plus `.local`.
A non-`.local` DNS name is also supported, but you must arrange DNS so the Pi
and tablets resolve that name to this Pi before activation.

Options:

```sh
sudo bash install.sh --host gatekeeper-north.local --hdmi-connector HDMI-A-2
# Advanced: omit the optional maintenance editor:
sudo bash install.sh --host gatekeeper-north.local --no-editor
# Advanced: preserve already-tested HDMI boot settings:
sudo bash install.sh --host gatekeeper-north.local --skip-hdmi
```

The installer copies source to a new root-owned directory under `/root`,
records source hashes, and runs from that protected copy. It does not execute
npm lifecycle scripts as root. Prompts are intentional:

| Stage | What to enter or confirm |
|---|---|
| Initial authorization | `INSTALL-FRESH-GATEKEEPER` |
| Empty Node-RED/MQTT foundation | `INSTALL-EMPTY-BACKEND` |
| Ethernet roles | Actual management and isolated-gate adapter names from the displayed MAC/driver list; then `ISOLATED-GATE-SWITCH` and `SAVE-ETHERNET-ROLES` |
| Static gate link | An unused Pi address on the PLC's `/24`, then `APPLY-STATIC-GATE-LINK` |
| TLS/SQLite | The selected broker hostname exactly |
| First application administrator | Site name, a new 12-128 character password twice, then `CREATE-APP-ADMIN`. Username is fixed to `admin`; no Linux account is created. |
| Optional editor | Separate maintenance editor username/password; then `ENABLE-LOCAL-HTTPS-EDITOR` |
| HDMI | `HDMI-A-1` or `HDMI-A-2`, matching the selected connector |

Example addresses, **not automatically applied**:

- Site management network: your existing LAN.
- PLC: `10.10.10.11/24`.
- Pi gate port: an unused `10.10.10.3/24`.
- No default gateway on the isolated gate port.

Choose addresses for the new site; do not blindly reuse these on a connected
network. The helper checks adapter mapping, subnet conflicts, duplicate-address
probes and management-route preservation. A failed check is a stop, not an
instruction to disable it.

### What gets installed

Node.js/npm, pinned Node-RED/SQLite/QR packages, Mosquitto and Dynamic Security,
SQLite, OpenSSL, network tools, `qrencode`, and mDNS services. DHCP-server
packaging is installed with `dnsmasq` runtime-masked, then left disabled/inactive
until an administrator deliberately selects gate DHCP-server mode.

Each Pi receives its own generated CA, TLS key, manager identity, credential
secret, encrypted Node-RED credential file and empty SQLite database.
No reference-site users, gates, tablets, history or private keys are imported.
Ethernet roles are saved by MAC/driver, not assumed from an adapter name.

At completion the selected server flow is installed, platform helpers are
running, and **the GateKeeper backend is stopped**. No gates are registered.

## 4. Start and check

Keep actuator/motor power isolated:

```sh
sudo systemctl start gatekeeper-backend.service
systemctl is-active gatekeeper-backend.service mosquitto.service \
  gatekeeper-network-static.service gatekeeper-reference-management.service
```

All four should report `active`. The backend is enabled to start on subsequent
boots. If HDMI boot settings changed, reboot when ready:

```sh
sudo reboot
```

After reboot, verify services again, log in on the HDMI console, and follow
[FIRST-TABLET.md](FIRST-TABLET.md).

The tablet network must reach the configured hostname on **TCP 8883**.
The installer does not configure router/firewall rules. Keep the PLC network
isolated; do not expose MQTT or Node-RED to the public internet.

Port 1883 is anonymous **loopback only**. The optional HTTPS editor is
**127.0.0.1:1880 only**, reached over an SSH tunnel, not the operator dashboard.

## 5. If installation stops

Read the exact error and last printed stage. The protected source directory
contains `install-state.json`. Generated keys/data are preserved, and a failure
after foundation installation attempts to leave the backend stopped.

There is **no automatic resume, reinstall-over-existing, or automatic rollback
of a partially completed fresh install**. Do not delete the live database, CA,
manager accounts or service folders to force the installer to pass. Diagnose
the failed stage first. On a genuinely disposable new card, reimaging is the
cleanest full restart, after confirming there is no state to preserve.

If dependency installation failed while masked, `dnsmasq` may remain
runtime-masked. Review locally before unmasking; do not start a DHCP server on
the management network.

The clean-card end-to-end test remains outstanding. Record the OS image,
hardware/adapters, completed prompts, restart behavior and first physical QR
scan when testing this path on the next Pi.
