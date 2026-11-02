"""Real isolated TLS MQTT + local socket enrollment; no live data, OS commands or PLC access."""

import json
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time

from support import ROOT, load, port, stop

sys.path.insert(0, str(ROOT / "scripts"))
from server_source import render


def connect(provision, host, tls_port, ca, username, password):
    context = ssl.create_default_context(cafile=str(ca))
    raw = socket.create_connection(("127.0.0.1", tls_port), timeout=10)
    stream = context.wrap_socket(raw, server_hostname=host)
    body = b"\x00\x04MQTT\x04\xc2\x00\x0a" + provision.mqtt_string(secrets.token_hex(8))
    body += provision.mqtt_string(username) + provision.mqtt_string(password)
    stream.sendall(provision.packet(0x10, body))
    if provision.receive_packet(stream) != (0x20, b"\x00\x00"):
        stream.close()
        raise ValueError("Isolated MQTT login failed.")
    return stream


def subscribe(provision, stream, topic):
    stream.sendall(provision.packet(0x82, b"\x00\x01" + provision.mqtt_string(topic) + b"\x00"))
    if provision.receive_packet(stream) != (0x90, b"\x00\x01\x00"):
        raise ValueError("Private subscription failed.")


def reply(provision, stream, topic):
    kind, body = provision.receive_packet(stream, max_length=500000)
    if kind not in (0x30, 0x32):
        raise ValueError("Expected MQTT reply.")
    length = int.from_bytes(body[:2], "big")
    if body[2:2 + length].decode() != topic:
        raise ValueError("Reply topic mismatch.")
    offset = 2 + length
    if kind == 0x32:
        stream.sendall(provision.packet(0x40, body[offset:offset + 2]))
        offset += 2
    return json.loads(body[offset:])


def main():
    provision = load("isolated_provision", "gatekeeper-provision")
    display = load("isolated_display", "gatekeeper-enroll")
    admin = load("isolated_admin", "gatekeeper-first-admin")
    host = "isolated-gatekeeper.local"
    with tempfile.TemporaryDirectory(prefix="gatekeeper-qr-test-") as directory:
        root = Path(directory)
        provision.prepare(root, host)
        with sqlite3.connect(root / "system.db") as database:
            admin.create_first_admin(database, "admin", "test-password-only", "Isolated site")
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        (runtime / "node_modules").symlink_to("/opt/gatekeeper-backend/node_modules")
        local_port, tls_port = port(), port()
        plugin = next(Path("/usr/lib").glob("*/mosquitto_dynamic_security.so"))
        config = root / "broker.conf"
        config.write_text("per_listener_settings true\n"
                          f"listener {local_port} 127.0.0.1\nallow_anonymous true\n" +
                          provision.broker_config(root / "tls", root / "dynamic-security.json",
                                                  plugin, True, tls_port).replace(
                                                      "per_listener_settings true\n", ""))
        flows = render(host)
        nodes = {node["id"]: node for node in flows}
        for node in flows:
            if node["type"] in ("exec", "http request", "tcp request") or (
                    node["type"] == "inject" and node["id"] != "commissioning_start"):
                node["d"] = True
            if node["type"] == "sqlitedb":
                node["db"] = str(root / "system.db")
            if node["type"] == "file":
                node["filename"] = str(runtime / (node["id"] + ".log"))
        nodes["local_mqtt_broker"]["port"] = str(local_port)
        nodes["tablet_dynsec_broker"].update(broker="127.0.0.1", port=str(tls_port))
        nodes["tablet_dynsec_tls"]["ca"] = str(root / "tls/ca.crt")
        nodes["commissioning_socket"]["initialize"] = nodes["commissioning_socket"]["initialize"].replace(
            "/var/lib/gatekeeper/commissioning.sock", str(runtime / "commissioning.sock")).replace(
                "/etc/gatekeeper/broker-ca.crt", str(root / "tls/ca.crt"))
        (runtime / "flows.json").write_text(json.dumps(flows))
        credentials = {"tablet_dynsec_broker": {"user": provision.MANAGER,
                                               "password": (root / "manager-password").read_text().strip()}}
        credential_input = root / "credentials.json"
        credential_input.write_text(json.dumps(credentials))
        credential_input.chmod(0o600)
        secret = root / "credential-secret"
        secret.write_text(secrets.token_hex(32))
        subprocess.run(["node", "-"], cwd=runtime, text=True, check=True, capture_output=True, input=(
            "const fs=require('fs'),crypto=require('crypto');"
            "const key=crypto.createHash('sha256').update(fs.readFileSync('../credential-secret','utf8').trim()).digest();"
            "const iv=crypto.randomBytes(16),cipher=crypto.createCipheriv('aes-256-ctr',key,iv);"
            "const encrypted=Buffer.concat([cipher.update(fs.readFileSync('../credentials.json')),cipher.final()]);"
            "fs.writeFileSync('flows_cred.json',JSON.stringify({$:iv.toString('hex')+encrypted.toString('base64')}),"
            "{mode:0o600});"))
        settings = (ROOT / "deployment/settings.js").read_text().replace(
            "/etc/gatekeeper/credential-secret", str(secret)).replace(
            "/etc/gatekeeper/maintenance-editor.json", str(root / "no-editor.json"))
        (runtime / "settings.js").write_text(settings)
        with (root / "runtime.log").open("w") as log:
            broker = subprocess.Popen(["/usr/sbin/mosquitto", "-c", str(config)], stdout=log, stderr=log)
            backend = None
            try:
                time.sleep(1)
                backend = subprocess.Popen(
                    ["node", "/opt/gatekeeper-backend/node_modules/node-red/red.js", "--userDir", str(runtime),
                     "--settings", str(runtime / "settings.js")], cwd=runtime, stdout=log, stderr=log)
                for _ in range(30):
                    time.sleep(1)
                    try:
                        with socket.socket(socket.AF_UNIX) as probe:
                            probe.settimeout(3)
                            probe.connect(str(runtime / "commissioning.sock"))
                            probe.sendall(b'{"action":"status"}\n')
                            status, _ = display.read_record(probe, b"")
                    except (OSError, ValueError):
                        continue
                    if status.get("ready"):
                        break
                else:
                    raise ValueError("Isolated local enrollment socket did not become ready.")
                with socket.socket(socket.AF_UNIX) as local:
                    local.settimeout(15)
                    local.connect(str(runtime / "commissioning.sock"))
                    local.sendall(b'{"action":"create","deviceName":"First tablet test"}\n')
                    issued, _ = display.read_record(local, b"")
                    if not issued.get("ok"):
                        raise ValueError("QR creation failed.")
                    qr = json.loads(issued["qrText"])
                    if qr["schema"] != "revpi-tablet-enrollment/v1" or qr["broker"]["host"] != host:
                        raise ValueError("QR schema/hostname mismatch.")
                    ca = (root / "tls/ca.crt").read_text()
                    encoded_ca = ca.replace("-----BEGIN CERTIFICATE-----", "").replace(
                        "-----END CERTIFICATE-----", "").replace("\n", "")
                    if qr["caCertificate"] != encoded_ca:
                        raise ValueError("QR CA mismatch.")
                    image = subprocess.run(
                        ["qrencode", "-t", "XPM", "-s", "1", "-m", "0", "-l", "L", "-o", "-"],
                        input=issued["qrText"], text=True, capture_output=True, check=True)
                    matrix = display.console_module().parse_xpm(image.stdout)
                    if 720 // len(matrix) < 3:
                        raise ValueError("QR does not meet minimum 720p module size.")
                    bootstrap = qr["bootstrap"]
                    permanent_password = secrets.token_hex(16)
                    with connect(provision, host, tls_port, root / "tls/ca.crt",
                                 bootstrap["username"], bootstrap["password"]) as stream:
                        topic = "tablet/enroll/reply/" + bootstrap["username"]
                        subscribe(provision, stream, topic)
                        request = {"requestId": "first-device", "enrollmentId": qr["device"]["id"],
                                   "token": bootstrap["token"], "deviceName": qr["device"]["name"],
                                   "expiresAt": bootstrap["expiresAt"], "permanentPassword": permanent_password}
                        stream.sendall(provision.packet(0x30, provision.mqtt_string(
                            "tablet/enroll/request/" + bootstrap["username"]) + json.dumps(request).encode()))
                        result = reply(provision, stream, topic)
                        if result.get("success") is not True:
                            raise ValueError("Enrollment redemption failed.")
                    ended, _ = display.read_record(local, b"")
                    if not ended.get("ended"):
                        raise ValueError("Local QR did not clear after redemption.")
                    tag = result["mqtt"]["username"]
                    with connect(provision, host, tls_port, root / "tls/ca.crt",
                                 tag, result["mqtt"]["password"]) as stream:
                        topic = "system/auth/reply/" + tag
                        subscribe(provision, stream, topic)
                        stream.sendall(provision.packet(0x30, provision.mqtt_string(
                            "tablet/auth/request/" + tag) + json.dumps({
                                "username": "admin", "password": "test-password-only",
                                "device": "First tablet test"}).encode()))
                        if reply(provision, stream, topic).get("success") is not True:
                            raise ValueError("Permanent-device application login failed.")
                    for _ in range(15):
                        accounts = json.loads((root / "dynamic-security.json").read_text())["clients"]
                        if not any(account["username"].startswith("enroll_") for account in accounts):
                            break
                        time.sleep(1)
                    else:
                        raise ValueError("Used bootstrap account not cleaned up.")
                    if not any(account["username"] == tag for account in accounts):
                        raise ValueError("Permanent account not persisted.")
                print("Local QR passed: embedded CA, host, readable matrix, TLS redemption, "
                      "permanent-device login and single-use account cleanup. No live PLC/OS actions.")
            finally:
                if backend:
                    stop(backend)
                stop(broker)


if __name__ == "__main__":
    main()
