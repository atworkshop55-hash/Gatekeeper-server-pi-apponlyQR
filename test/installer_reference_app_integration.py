"""Exercise the extracted reference app flow using private MQTT, SQLite and a loopback PLC."""

import json
from pathlib import Path
import socket
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import sys

from support import load, port, stop, receive


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from server_source import render


def main():
    database_builder = load("reference_acceptance_database", "gatekeeper-install")
    with tempfile.TemporaryDirectory(prefix="gatekeeper-reference-app-") as directory:
        root = Path(directory)
        database_path = root / "system.db"
        with sqlite3.connect(database_path) as database:
            database_builder.prepare_database(database)
            database.execute("INSERT INTO users VALUES('admin','reference-test-password','admin')")
            database.execute("UPDATE settings SET value='Reference app test' WHERE key='siteName'")
            database.execute("INSERT INTO zones VALUES('test_zone','Test zone')")
            database.execute("INSERT INTO gates VALUES(?,?,?,?,?,?)",
                             ("test_gate", "Reference test gate", "10.10.10.11",
                              "02:11:22:33:44:55", 0, "test_zone"))
            identity = database.execute("SELECT * FROM gates").fetchall()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.2)
        stopping = threading.Event()
        packets, errors = [], []
        platform_requests = []
        platform_listener = socket.socket(socket.AF_UNIX)
        platform_listener.bind(str(root / "platform.sock"))
        platform_listener.listen(2)
        platform_listener.settimeout(0.2)

        def platform_simulate():
            try:
                while not stopping.is_set():
                    try:
                        stream, _ = platform_listener.accept()
                    except socket.timeout:
                        continue
                    with stream:
                        stream.settimeout(3)
                        data = b""
                        while b"\n" not in data:
                            chunk = stream.recv(4096)
                            if not chunk or len(data) + len(chunk) > 4096:
                                raise ValueError("Invalid fixture platform request.")
                            data += chunk
                        request = json.loads(data)
                        platform_requests.append(request)
                        if request.get("action") != "apply_gate":
                            raise ValueError("Unexpected fixture platform action.")
                        response = {"ok": True, "message": "Isolated network result.",
                                    "config": dict(request["config"], interface="eth1"),
                                    "address": "10.10.10.3"}
                        stream.sendall((json.dumps(response) + "\n").encode())
            except (OSError, ValueError) as error:
                errors.append(error)
                stopping.set()

        def simulate():
            feedback = 0xB8
            try:
                while not stopping.is_set():
                    try:
                        stream, peer = listener.accept()
                    except socket.timeout:
                        continue
                    with stream:
                        if peer[0] != "127.0.0.1":
                            raise ValueError("Non-loopback peer.")
                        stream.settimeout(3)
                        header = receive(stream, 7)
                        transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                        if protocol != 0 or unit != 1 or length not in (6, 8):
                            raise ValueError("Unexpected PLC framing.")
                        packet = header + receive(stream, length - 1)
                        packets.append(packet)
                        if packet[7:] == bytes.fromhex("0100080008"):
                            reply = struct.pack(">HHHBBBB", transaction, 0, 4, 1, 1, 1, feedback)
                        elif len(packet) == 14 and packet[7:13] == bytes.fromhex("0f0000000801"):
                            value = packet[13]
                            if value & 0x80 or value & 6 == 6:
                                raise ValueError("Invalid simulated outputs.")
                            if value & 0x10 and value & 0x20:
                                feedback &= ~0x20
                            if value & 2:
                                feedback &= ~8
                            reply = struct.pack(">HHHBBHH", transaction, 0, 6, 1, 15, 0, 8)
                        else:
                            raise ValueError("Unexpected simulated Modbus function.")
                        stream.sendall(reply)
            except (OSError, ValueError) as error:
                errors.append(error)
                stopping.set()

        thread = threading.Thread(target=simulate)
        thread.start()
        platform_thread = threading.Thread(target=platform_simulate)
        platform_thread.start()
        processes = []
        try:
            broker_port = port()
            flows = render("gatekeeper-test.local")
            nodes = {node["id"]: node for node in flows}
            timers = {"c5b2b68381c62d5f", "97f27df58dde2b1d", "219e58c67ebb3f00", "tablet_session_tick"}
            for node in flows:
                if node["type"] in ("exec", "http request"):
                    node["d"] = True
                if node["type"] == "inject" and node["id"] not in timers:
                    node["d"] = True
                if node["type"] == "sqlitedb":
                    node["db"] = str(database_path)
                if node["type"] == "mqtt-broker":
                    node.update(broker="127.0.0.1", port=str(broker_port), tls="", usetls=False)
                if node["type"] == "tls-config":
                    node.update(ca="", cert="", key="", servername="")
                if node["type"] == "file":
                    node["filename"] = str(root / (node["id"] + ".log"))
            nodes["commissioning_socket"].update(initialize="", finalize="")
            nodes["pi_telemetry_sample"]["d"] = True
            core = nodes["e14a2263d2ba3f21"]["z"]
            original_wires = nodes["e14a2263d2ba3f21"]["wires"]
            nodes["e14a2263d2ba3f21"]["wires"] = [["fixture_tcp_redirect"]]
            flows.extend([
                {"id": "fixture_tcp_redirect", "type": "function", "z": core, "outputs": 1,
                 "wires": original_wires,
                 "func": "if(!['10.10.10.11','10.10.10.12'].includes(msg.host)||msg.port!==502)"
                         "throw Error('Unexpected PLC');"
                         "msg.host='127.0.0.1';msg.port=" + str(listener.getsockname()[1]) + ";return msg;"},
                {"id": "fixture_discovery_tick", "type": "inject", "z": core, "once": True,
                 "onceDelay": "0.5", "repeat": "", "wires": [["fixture_discovery_state"]]},
                {"id": "fixture_discovery_state", "type": "function", "z": core, "outputs": 1,
                 "wires": [["f932e9b87c26bf53"]],
                 "func": "global.set('hwStats',{sampledAt:Date.now(),interface1:'eth1',linkB:true});return msg;"},
            ])
            (root / "flows.json").write_text(json.dumps(flows))
            (root / "settings.js").write_text(
                "module.exports={userDir:" + json.dumps(str(root)) + ",flowFile:'flows.json',"
                "httpAdminRoot:false,httpNodeRoot:false,disableEditor:true,"
                "credentialSecret:'isolated-reference-only',functionExternalModules:false,"
                "externalModules:{autoInstall:false},logging:{console:{level:'warn'}},"
                "functionGlobalContext:{commissioningCrypto:require('crypto'),"
                "commissioningNet:{createConnection:target=>{"
                "if(target!=='/run/gatekeeper-network/control.sock')throw Error('Unexpected socket');"
                "return require('net').createConnection(" + json.dumps(str(root / "platform.sock")) + ");}},"
                "commissioningChildProcess:{execFile:(file,args,options,callback)=>{"
                "if(file!=='/usr/bin/ip'||args.join(' ')!=='-j -4 neighbour show dev eth1')"
                "throw Error('Unexpected fixture command');"
                "callback(null,JSON.stringify([{dst:'10.10.10.12',lladdr:'02:11:22:33:44:66',"
                "state:['REACHABLE']}]));}},"
                "commissioningQR:require('/opt/gatekeeper-backend/node_modules/qrcode')}};")
            (root / "broker.conf").write_text(
                f"listener {broker_port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
            with (root / "runtime.log").open("w") as log:
                broker = subprocess.Popen(["/usr/sbin/mosquitto", "-c", str(root / "broker.conf")],
                                          stdout=log, stderr=log)
                processes.append(broker)
                time.sleep(0.5)
                runtime = subprocess.Popen(
                    ["node", "/opt/gatekeeper-backend/node_modules/node-red/red.js",
                     "--settings", str(root / "settings.js")], cwd=root, stdout=log, stderr=log)
                processes.append(runtime)
                client = subprocess.Popen(
                    ["node", str(ROOT / "test/reference-app-mqtt-client.js"), str(broker_port)],
                    stdout=log, stderr=log)
                processes.append(client)
                try:
                    client.wait(timeout=60)
                    if client.returncode or errors:
                        raise ValueError("Reference tablet/PLC acceptance failed.")
                except (ValueError, subprocess.TimeoutExpired) as error:
                    raise ValueError(str(error) + "\n" +
                                     (root / "runtime.log").read_text()[-7000:]) from error
                stop(runtime)
            with sqlite3.connect(database_path) as database:
                if database.execute("SELECT * FROM gates WHERE id='test_gate'").fetchall() != identity:
                    raise ValueError("Reference control changed saved gate identity.")
                if database.execute("SELECT name,ip,mac,autoDhcp,zone FROM gates WHERE id<>'test_gate'").fetchall() != [
                        ("Discovered test gate", "10.10.10.12", "02:11:22:33:44:66", 0, "test_zone")]:
                    raise ValueError("Discovered gate was not persisted correctly.")
                config = database.execute("SELECT value FROM settings WHERE key='dhcpConf'").fetchone()
                if not config or json.loads(config[0]).get("staticIp") != "10.10.10.3":
                    raise ValueError("Network result was not persisted.")
                audits = database.execute("SELECT category,text FROM audit_logs").fetchall()
                for category, text in (("REMOTE_OPERATION", "OPEN command pressed"),
                                       ("AUTH", "logged in on tablet")):
                    if not any(row[0] == category and text in row[1] for row in audits):
                        raise ValueError("Missing reference audit: " + text)
            if not any(packet[7] == 15 and packet[13] & 2 for packet in packets):
                raise ValueError("Reference OPEN was not sent to loopback PLC.")
            if not any(packet[7] == 15 and packet[13] & 0x20 for packet in packets):
                raise ValueError("Reference ENERGIZE was not sent to loopback PLC.")
            if len(platform_requests) != 1:
                raise ValueError("Expected exactly one isolated platform request.")
            print("Extracted reference flow passed real MQTT login/acquisition, unchanged Key/Energize/OPEN "
                  "with V1.1 low, FC1/FC15 loopback, release/heartbeat loss, SQLite identity and audits. "
                  "Mapped discovery, real admin routes, network-result persistence and discovered-gate "
                  "registration also passed. Platform service and PLC are isolated simulators; "
                  "physical OS actions and local QR socket disabled.")
        finally:
            for process in reversed(processes):
                stop(process)
            stopping.set()
            thread.join(timeout=5)
            platform_thread.join(timeout=5)
            listener.close()
            platform_listener.close()
            if thread.is_alive() or platform_thread.is_alive():
                raise ValueError("Reference loopback simulator did not stop.")


if __name__ == "__main__":
    main()
