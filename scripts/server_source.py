"""Render the selected working server flow without site data or credentials."""

import copy
import hashlib
import ipaddress
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
CONTROL_IDS = ("2d379880daccf4e7", "70b093f7936f5d97", "87646df64e36bbe4",
               "95bfc858aa0a0b14", "e14a2263d2ba3f21", "bff45eeab1a72b03",
               "tablet_session_enforcer")
SOURCE_HOST = "gatekeeper-nexus.local"


def validate_host(host):
    host = host.lower()
    if "." not in host or len(host) > 253 or not all(re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
        raise ValueError("Use a resolvable DNS hostname, e.g. gatekeeper-nexus.local, not an IP or URL.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError("The TLS broker needs a DNS hostname, not an IP address.")


def validate_flows(flows):
    if not isinstance(flows, list) or not flows:
        raise ValueError("Nonempty Node-RED flow array required.")
    nodes = {}
    for node in flows:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or node["id"] in nodes:
            raise ValueError("Invalid/duplicate flow node.")
        if not isinstance(node.get("type"), str):
            raise ValueError("Node type required.")
        if node["type"].startswith(("ui_", "revpi-")) or node["type"] in ("http in", "http response"):
            raise ValueError("Operator web/dashboard or vendor I/O is not part of this server.")
        if node.get("credentials"):
            raise ValueError("Runtime credentials must never be embedded in the flow source.")
        nodes[node["id"]] = node
    for node in flows:
        targets = [target for group in node.get("wires", []) for target in group]
        targets += [node["z"]] if node.get("z") else []
        if node["type"] in ("mqtt in", "mqtt out") and node.get("broker"):
            targets.append(node["broker"])
        if node["type"] == "sqlite" and node.get("mydb"):
            targets.append(node["mydb"])
        if node["type"] == "mqtt-broker" and node.get("tls"):
            targets.append(node["tls"])
        for key in ("links", "scope"):
            if isinstance(node.get(key), list):
                targets += node[key]
        if any(target not in nodes for target in targets):
            raise ValueError("Dangling flow reference on " + node["id"])
    for identifier in (*CONTROL_IDS, "commissioning_socket", "tablet_dynsec_broker",
                       "pi_telemetry_sample", "ade6a257a7680761"):
        if identifier not in nodes:
            raise ValueError("Missing selected server handler: " + identifier)
    return nodes


def render(host):
    host = validate_host(host)
    flows = json.loads((ROOT / "deployment" / "reference-flows.json").read_text())
    nodes = validate_flows(flows)
    before = copy.deepcopy(nodes)
    nodes["tablet_dynsec_broker"]["broker"] = host
    nodes["tablet_dynsec_tls"]["servername"] = host
    marker = "global.set('commissioning_host'," + json.dumps(SOURCE_HOST) + ");"
    if marker not in nodes["commissioning_socket"]["initialize"]:
        raise ValueError("Expected local QR broker-host configuration.")
    nodes["commissioning_socket"]["initialize"] = nodes["commissioning_socket"]["initialize"].replace(
        marker, "global.set('commissioning_host'," + json.dumps(host) + ");", 1)
    for identifier, filename in (
            ("ce56bd4800b3ee71", "reference-neighbours.js"),
            ("f932e9b87c26bf53", "reference-neighbours.js"),
            ("4871d55b23e6a58d", "reference-platform-request.js"),
            ("reference_reboot", "reference-platform-request.js"),
            ("50ac9e8b9c784abf", "reference-os-result.js"),
            ("pi_telemetry_sample", "pi-telemetry.js")):
        nodes[identifier]["func"] = (ROOT / "deployment" / filename).read_text()
    for identifier in CONTROL_IDS:
        if nodes[identifier] != before[identifier]:
            raise ValueError("Original reference control changed during rendering.")
    validate_flows(flows)
    return flows


def render_file(destination, host):
    flows = render(host)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(flows, stream, indent=2)
        stream.write("\n")
    destination.chmod(0o644)
    return hashlib.sha256(destination.read_bytes()).hexdigest()
