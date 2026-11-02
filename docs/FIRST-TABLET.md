# Add the first tablet using the local HDMI monitor

This is the way to bootstrap the **first** device. It needs no already-enrolled
tablet and no operator web dashboard. The working flow's local enrollment tool
authorizes a temporary identity, displays the QR directly on the Pi's screen,
and exchanges it for a permanent tablet MQTT account.

## Understand the three logins

| Login | Purpose |
|---|---|
| Linux maintenance account + sudo | Authorize enrollment at the Pi's physical console |
| Tablet's generated MQTT identity | Connect that particular device to this server; created by scanning the QR |
| Application user (`admin` initially) | Sign into GateKeeper after device enrollment |

The optional Node-RED editor has a **separate** maintenance login.
Scanning a QR does not create an application user or give the tablet Linux access.

## 1. Check prerequisites

1. Finish [installation](INSTALL.md) and deliberately start the backend.
2. Ensure the first application administrator was created. Its username is
   `admin`, and its password is the one entered at `CREATE-APP-ADMIN`.
3. Connect the tablet to the site's management Wi-Fi/LAN. It must resolve the
   configured broker hostname and reach it on TCP 8883. The Pi does not create a
   Wi-Fi access point automatically.
4. Install the compatible GateKeeper tablet app separately. The server
   repository does not contain an APK or build/install the Flutter app.
5. Set sensible date/time on both devices, so TLS certificates validate.
6. Connect the HDMI monitor and keyboard to the Pi. Use a Linux **text console**,
   not an SSH window or a desktop terminal. On a desktop, switch to a text VT
   (for example Ctrl+Alt+F2) and log in, rather than drawing over the desktop.
7. Leave actuator/motor power isolated during first-device and gate testing.

## 2. Log in locally on the Pi

At the HDMI login prompt, log in with the Linux maintenance account created in
Raspberry Pi Imager. Enrollment is not an autologin kiosk and does not display
credentials at an unattended boot screen.

From that **physical console**, run:

```sh
sudo python3 -B /opt/gatekeeper-server-tools/gatekeeper-enroll --status
```

On the earlier preserving-upgrade Pi, these tools may not yet be under `/opt`.
From this repository's reviewed clone, use
`sudo python3 -B scripts/gatekeeper-enroll --status` instead, and omit `--status`
to display the real QR. This does not reinstall the system.

Enter the Linux sudo password if requested. Look for:

- `enrollment_ready: true`
- At least one `application_administrators`
- On a new system, `permanent_tablet_accounts: 0`
- Normally `bootstrap_accounts_remaining: 0`

Some capability/readiness flags in this retained reference profile describe
older staged-login/admin modules and may be false. Do not use those flags as a
substitute for the actual login test below. Enrollment readiness and successful
device redemption are the relevant checks at this step.

## 3. Generate and display the one-use QR

Still on the Pi's physical HDMI console:

```sh
sudo python3 -B /opt/gatekeeper-server-tools/gatekeeper-enroll
```

At `Name for the new tablet:`, enter a recognizable device name, for example:

```text
North Gate Tablet 1
```

The name can be 1-64 characters without control characters. Wait for the large
black-and-white QR to appear. Do not press a key while preparing to scan it.

The QR contains:

- The server hostname and TLS broker port.
- The site's **public** CA certificate, so this path needs no separate manual
  CA import.
- A generated device identity and a temporary, restricted bootstrap account/token.

It is **five-minute, single-use enrollment access**, not a permanent public
connection QR. Do not photograph, email, paste into chat, commit, or share it.
It is generated locally, without an external QR service.

## 4. Scan on the tablet

1. Open the app's **Connection Settings**. This must be accessible before
   application login.
2. Select **ENROLL TABLET BY QR**.
3. Allow camera permission if requested.
4. Point the camera at the QR on the **Pi's local monitor**. Fill enough of the
   camera view to scan, while keeping all four corners and the white border
   visible. Reduce glare or increase distance slightly if focus is poor.
5. Keep the Pi display open while the app redeems the code.
6. Wait for the app's confirmation:
   **"Tablet enrolled. Its MQTT credentials were saved."**

The app validates the payload, imports the embedded CA, redeems the bootstrap
account, and saves the permanent per-device MQTT credentials. Do not replace
those generated credentials with the application username/password.

The current Windows app uses its enrollment input dialog rather than assuming
a camera. These steps describe the camera-equipped tablet path; use that app's
supported enrollment method on Windows.

Some older app help still mentions a Node-RED Tablets page. In this server
profile the **first-device QR is on the physical Pi monitor**, not a dashboard.

## 5. Confirm the QR cleared and log in

After successful redemption, the QR should disappear and the console return.
The screen also clears on five-minute expiry, any key or Ctrl+C. Cancelling
closes the local request and invalidates unused enrollment access; create a
new QR rather than trying to reuse it.

On the tablet:

1. Return to the application login screen.
2. Enter username **`admin`**.
3. Enter the **application** password set during installation, not the Linux
   sudo or Node-RED editor password.
4. Sign in. On a fresh database an empty gate list is expected.

At the Pi's physical console, check again:

```sh
sudo python3 -B /opt/gatekeeper-server-tools/gatekeeper-enroll --status
```

Expected after the first successful enrollment:

- `enrollment_ready: true`
- `permanent_tablet_accounts: 1`
- `bootstrap_accounts_remaining: 0` after cleanup

Each additional tablet needs its **own** fresh QR and permanent account.
Do not clone an enrolled tablet's credentials onto another device.

## 6. Register the actual controller

Using the signed-in tablet's administrator/network screen:

1. Verify the isolated gate interface and configured Pi address are correct.
2. Power the PLC logic and check the Ethernet link.
3. Refresh network inventory/discovered controllers.
4. Identify the real controller by its **known IP and MAC**, not just its
   generic discovery label.
5. Select it, enter a unique gate name and appropriate zone, and add it.
6. Check it appears online and feedback updates.

Discovery reads the kernel's neighbour table; it is **not** a guaranteed full
subnet scan or proof that an address is a PLC. If the expected PLC is missing,
verify cabling/subnet and ping its known IP from the Pi, then refresh:

```sh
ping -c 3 10.10.10.11
ip -4 neighbour
```

Replace the example IP with the real PLC address. Never register arbitrary
neighbours or add synthetic database rows just to populate the list.

Only then perform deliberate Key/Energize, Open/Close, stop, release,
disconnection and restart acceptance with actuator power isolated and the
appropriate physical safety arrangements. Enrollment alone does not commission
an operational gate.

## Optional SSH-assisted display on the physical monitor

This is a **separate, opt-in site permission**, not proof the maintainer is
physically present. Normal local-console enrollment remains available. The QR
still appears only on the Pi monitor, never in the SSH terminal or a QR file.
Use authorized SSH on the site LAN or VPN, not a new internet-facing endpoint.

On an existing working server, install just the reviewed commissioning tools:

```sh
sudo bash deployment/install-enrollment-tools --enable-ssh-display
```

Run this from this repository's reviewed clone. It does **not** reinstall the
server, restart the backend, change flows, disable tablet enrollment in the app,
or send PLC commands. Root/sudo authorization is still required for every request.
Fresh installations include the short local command, but do not enable SSH
issuance by default.

From an interactive SSH session (use `ssh -t` when specifying a remote command):

```sh
gatekeeper-enroll --ssh-display --status
gatekeeper-enroll --ssh-display --display-test --name Display-test
gatekeeper-enroll --ssh-display
```

The second command shows a harmless test QR for up to 60 seconds; it cannot enroll
a tablet. Confirm that it appears on the actual monitor, not just that the command
runs. Any key in SSH or on the local keyboard cancels the display.

The last command asks for the new tablet's name, switches temporarily to an unused
Linux virtual console, and displays the five-minute single-use enrollment QR.
Keep SSH connected while the keeper scans using the steps above. On redemption,
expiry, cancellation, detected HDMI disconnect or console change, the display
clears and the previous console is restored. Disconnecting SSH requests cancellation;
an independent systemd runtime limit also bounds the worker if the SSH launcher dies.
Concurrent local/remote displays are refused. No unattended QR is shown at boot.

Local maintainers can now simply run:

```sh
gatekeeper-enroll
```

Disable new SSH requests at any time:

```sh
sudo rm /etc/gatekeeper/ssh-enrollment-enabled
```

This does not cancel an already-running display: cancel it in its SSH window,
press a key at the Pi, or wait for expiry. Local console enrollment is unaffected.
For failed SSH display requests, the command prints the exact temporary unit name;
inspect its local journal using that name. These commissioning logs do not include
the QR payload. Physical-monitor scanning and cleanup must be accepted on the
actual target; unit tests do not prove the display works.

## Troubleshooting

| Symptom | Check/action |
|---|---|
| "Authenticated HDMI Linux console required, not SSH" | Use the physical text console, or the separately enabled `--ssh-display` mode. Normal local mode still rejects SSH. |
| SSH policy file missing / not enabled | Ask the site administrator to deliberately install/enable the SSH display option above. Local enrollment remains available. |
| SSH display failed | Inspect the temporary service journal named in the error; check HDMI connection, available Linux VT, `/dev/fb0`, and whether another display is active. |
| No screen after a headless boot | Confirm the selected HDMI connector, KMS boot settings and reboot; try the configured 720p display. |
| Framebuffer geometry/error or QR too small | The tool supports arm64 RGB565/32-bit `/dev/fb0` at zero offset and at least three pixels/module. Use the configured 1280x720 console; it fails rather than displaying an unreadable QR. |
| Backend not ready / socket missing | Check the backend and Mosquitto services. Confirm the first app admin exists. Inspect errors locally; do not publish journals containing credentials. |
| Enrollment busy | Close an existing local QR display or wait for its five-minute expiry/cleanup; do not generate concurrent requests. |
| Hostname cannot resolve | Tablet and Pi must resolve the QR's hostname to this Pi. Check mDNS/DNS, unique hostname, VLANs and client isolation. Do not substitute an IP that is absent from the certificate. |
| TLS/certificate error | Correct both clocks and use the CA embedded in a newly generated QR for this server. Do not disable certificate checks. |
| QR expired/cancelled/already used | Generate a new QR locally; do not reuse screenshots or old tokens. |
| Device enrolled but app login fails | Use `admin` plus the application password; device authorization and application login are separate. |
| Tablet lost or replaced | Revoke that device's permanent identity through authenticated tablet administration, then enroll the replacement with a new QR. |

Keep unused monitors locked/logged out when maintenance is finished. A new
server/reimage has a different CA and account database: enroll devices for that
server, rather than expecting credentials for the old site to work.
