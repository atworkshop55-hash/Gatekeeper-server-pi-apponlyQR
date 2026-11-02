# Maintain the source and running server

## Git source is not the installed runtime

The clone is editable source. The running service uses:

| Location | Purpose |
|---|---|
| `/var/lib/gatekeeper/flows.json` | Deployed Node-RED server flow |
| `/var/lib/gatekeeper/flows_cred.json` | Encrypted runtime credentials; never commit/export |
| `/var/lib/gatekeeper/system.db` | Users, settings, zones, gates, audits |
| `/etc/gatekeeper/` | Protected site settings, secret, public CA and private CA material |
| `/opt/gatekeeper-backend/` | Root-owned runtime and pinned npm dependencies |
| `/opt/gatekeeper-network/` | Root-owned bounded platform service/helpers |
| `/opt/gatekeeper-server-tools/` | Local enrollment tools on a new install |
| `/etc/mosquitto/` and `/var/lib/mosquitto/` | TLS listener and permanent device identity state |

`git pull` updates the clone **only**. It does not deploy Node-RED flows,
replace databases, rotate keys or enroll/revoke devices. The fresh installer
intentionally refuses an already-installed system. Do not rerun it as an updater.

## Pull and amend

```sh
cd Gatekeeper-server-pi-apponlyQR
git status
git pull --ff-only
git switch -c my-server-change
```

Commit or otherwise preserve your own local changes before pulling. If Git
refuses a fast-forward or reports conflicts, resolve them deliberately; do not
force-reset working changes.

Important source files:

- `deployment/reference-flows.json`: selected app-only flow template. It contains
  the original tablet/control functions and configuration, **not** site accounts.
- `deployment/reference-neighbours.js`: mapped gate-interface discovery.
- `deployment/reference-platform-request.js`: bounded root-service socket client.
- `deployment/reference-os-result.js`: confirmed command-result formatting.
- `deployment/pi-telemetry.js`: mapped Pi telemetry.
- `scripts/server_source.py`: validates and renders host-specific flows; it
  synchronizes the above JavaScript files into their Function nodes.
- `scripts/install-server`: fresh orchestration.
- `scripts/revpi-network`, `scripts/revpi-management`,
  `scripts/gatekeeper-network-service`: privileged network boundary.
- `deployment/package.json` and `package-lock.json`: pinned runtime dependencies.

Edit the matching source, then run:

```sh
python3 -B -m unittest discover -s test -p '*_test.py'
node --test test/reference-platform.test.js
python3 -B test/installer_reference_app_integration.py
python3 -B test/enrollment_integration.py
```

The last two need the Pi's installed Node-RED/Mosquitto/QR dependencies and use
isolated fixtures. Do not run physical PLC commands as a substitute for tests.

Render a **new** file for this site's actual hostname:

```sh
mkdir -p build
python3 -B scripts/render-server --host gatekeeper-nexus.local build/server-flows.json
```

Rendering never deploys or starts a service. It refuses to overwrite an existing
output file. Remove only your specific obsolete generated output or choose a
new filename before rendering again. The original source placeholder hostname
is `gatekeeper-nexus.local`; the renderer updates the manager broker,
TLS server name and QR host together.

## HTTPS editor access

If installed, the editor listens only on `127.0.0.1:1880`.
From your maintenance computer:

```sh
ssh -N -L 1880:127.0.0.1:1880 YOUR_LINUX_USER@PI_MANAGEMENT_ADDRESS
```

Browse to `https://localhost:1880` and sign in with the **separate Node-RED
maintenance editor account**. Trust/import only the verified public site CA;
never disable TLS verification globally or transfer the CA private key.
If local port 1880 is occupied, forward a different local port, e.g.
`1881:127.0.0.1:1880`, and browse `https://localhost:1881`.

The editor can deploy arbitrary code as the service account. Give access only
to trusted maintainers. It is not the removed operator dashboard.

## Deploy an amendment deliberately

The repository does not yet include a general in-place upgrade/migration tool.
For a flow-only amendment, use the authenticated maintenance editor under a
controlled maintenance window:

1. Isolate actuator power and release gate sessions.
2. Take a consistent protected backup of the current flows, credential file,
   SQLite database, settings/credential secret, broker TLS and Dynamic Security
   state. Stop the backend and broker during filesystem snapshots; use SQLite's
   backup facility or copy its database plus any WAL sidecars consistently.
   Record service state and file ownership/modes.
3. Keep the backup **outside the Git clone**, root-only and protected/offline.
   A flow export alone cannot restore enrolled devices or site identity.
4. Restart the unchanged services when the backup is complete. Open the editor,
   compare the rendered candidate against the current flow and preserve the
   broker/config node IDs used by the encrypted credential bindings.
5. Import/replace the reviewed flow, not append a second copy of the backend.
   There must be exactly one tablet-command ingress and one PLC polling path.
   Keep the same protected credential secret and runtime credential file.
6. Deploy only the intended changes. Verify service startup, local QR readiness,
   login, gate feedback, ownership/release, network inventory and auditing.
   Re-test control/disconnection/restart safely before restoring actuator power.

If dependency versions, root helpers, systemd profiles or schema need changing,
stop and plan that upgrade separately. Importing flows does not install npm
modules, root helpers or migrations. Do not patch live root helpers from an
unreviewed branch or grant blanket sudo to make a changed flow run.

When editing in Node-RED first, export **flows without credentials** and review
the diff before updating the template. Preserve the template's host placeholders
and configuration IDs, and copy platform Function edits into their corresponding
JavaScript source files as well. Never commit `flows_cred.json`, a site database,
Dynamic Security JSON, private keys, passwords, QR payloads or raw journals.

## Record source changes

```sh
git diff --check
git diff
git add deployment/reference-flows.json
# Add only the other specific source/docs/test files you changed.
git commit -m "Describe the server change"
git push -u origin my-server-change
```

Review changes before merging to the default branch. A commit or push does not
deploy anything. On another clone, pull the merged code and repeat the controlled
test/deployment steps, not the fresh-install command.

## Existing upgraded test Pi

The first working Pi was installed with the preserving reference upgrade, before
this standalone fresh installer existed. Its managed runtime/backup layout can
include `/var/lib/gatekeeper-control/ledger`; **preserve it**. Do not replace that
runtime just to make it look like a fresh installation.

You may clone this repository on that Pi for source, guides, rendering and
isolated tests. For local HDMI enrollment there, the tool can also run directly
from the reviewed clone:

```sh
cd Gatekeeper-server-pi-apponlyQR
sudo python3 -B scripts/gatekeeper-enroll
```

This must still be run on the physical text console, not SSH. It connects to
the existing local enrollment service and does not reinstall or replace site
data. If `/opt/gatekeeper-server-tools` does not exist on an older upgrade, use
this clone-based command rather than running the fresh installer.

## Checks and fault handling

```sh
systemctl is-active gatekeeper-backend.service mosquitto.service \
  gatekeeper-network-static.service gatekeeper-reference-management.service
sudo systemctl status gatekeeper-backend.service --no-pager
```

Inspect logs locally when needed; never paste credentials, raw QR contents or
unreviewed broker logs into public issues. If a network operation reports an
unknown outcome, inspect the real interface state locally before retrying.
Use confirmed management recovery rather than disconnecting your only uplink.

Backups must preserve the site's CA/keys and Dynamic Security state as well as
SQLite; losing one part can leave a tablet authorized in one layer but unable
to connect in another. Restoration must preserve ownership/modes and be tested
under a controlled maintenance window, not by copying a site onto a new server.
