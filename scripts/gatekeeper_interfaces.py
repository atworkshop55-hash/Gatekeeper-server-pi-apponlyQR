"""Validated physical Ethernet roles shared by privileged network helpers."""

import json
import ipaddress
import re
from pathlib import Path
import subprocess


MAPPING = Path("/etc/gatekeeper/interface-roles.json")
NET = Path("/sys/class/net")
ALIASES = {"management": ("lan0", "eth0"), "gate": ("lan1", "eth1")}


def inventory():
    devices = []
    for path in NET.iterdir():
        if (path / "wireless").exists() or not (path / "device").exists():
            continue
        if (path / "type").read_text().strip() != "1":
            continue
        name = path.name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", name):
            raise ValueError("Unsafe physical interface name.")
        mac = (path / "address").read_text().strip().lower()
        if not re.fullmatch(r"(?:[a-f0-9]{2}:){5}[a-f0-9]{2}", mac) or int(mac[:2], 16) & 1 or mac == "00:00:00:00:00:00":
            raise ValueError(f"Invalid Ethernet identity for {name}.")
        driver = (path / "device" / "driver").resolve(strict=True).name
        devices.append({"name": name, "mac": mac, "driver": driver})
    return devices


def validate_mapping(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported interface-role mapping.")
    roles = value.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ALIASES):
        raise ValueError("Both management and gate roles are required.")
    for role in ALIASES:
        item = roles[role]
        if not isinstance(item, dict) or set(item) != {"mac", "driver"} or not isinstance(item["mac"], str) or (
                not re.fullmatch(r"(?:[a-f0-9]{2}:){5}[a-f0-9]{2}", item["mac"])) or (
                int(item["mac"][:2], 16) & 1) or item["mac"] == "00:00:00:00:00:00" or (
                not isinstance(item["driver"], str)) or not re.fullmatch(r"[A-Za-z0-9_-]+", item["driver"]):
            raise ValueError(f"Invalid {role} adapter identity.")
    if roles["management"]["mac"] == roles["gate"]["mac"]:
        raise ValueError("Management and gate must be distinct adapters.")
    return value


def resolved_roles(value, devices):
    roles = validate_mapping(value)["roles"]
    result = {}
    for role, identity in roles.items():
        matches = [device for device in devices if device["mac"] == identity["mac"]]
        if len(matches) != 1 or matches[0]["driver"] != identity["driver"]:
            raise ValueError(f"{role} adapter is missing, duplicated or replaced; explicit remapping required.")
        result[role] = matches[0]["name"]
    if len(set(result.values())) != 2:
        raise ValueError("Interface roles resolve to the same adapter.")
    return result


def read_mapping():
    try:
        info = MAPPING.lstat()
    except FileNotFoundError:
        return None
    if MAPPING.is_symlink() or not MAPPING.is_file() or info.st_uid != 0 or info.st_mode & 0o777 != 0o640:
        raise ValueError("Unsafe interface-role mapping ownership/permissions.")
    return validate_mapping(json.loads(MAPPING.read_text()))


def resolve(role):
    if role not in ALIASES:
        raise ValueError("Unknown Ethernet role.")
    value = read_mapping()
    if value is not None:
        return resolved_roles(value, inventory())[role]
    for name in ALIASES[role]:
        if (NET / name).exists():
            return name
    raise RuntimeError(f"{role} interface is absent; configure physical roles first.")


def validate_request(role, requested):
    if role not in ALIASES:
        raise ValueError("Unknown Ethernet role.")
    if requested in ALIASES[role]:
        return
    value = read_mapping()
    if value is None or requested != resolved_roles(value, inventory())[role]:
        raise ValueError(f"Only the confirmed {role} port can be changed here.")


def ensure_gate_not_management(interface):
    result = subprocess.run(["ip", "-j", "-4", "route", "show", "table", "all"],
                            capture_output=True, text=True, timeout=10, check=True)
    if any(route.get("dst") == "default" and route.get("dev") == interface
           for route in json.loads(result.stdout)):
        raise ValueError("Gate adapter carries a default route; preserve management access and review roles.")


def ensure_gate_subnet(interface, address):
    network = ipaddress.IPv4Interface(address + "/24").network
    result = subprocess.run(["ip", "-j", "-4", "route", "show", "table", "all"],
                            capture_output=True, text=True, timeout=10, check=True)
    for route in json.loads(result.stdout):
        destination = route.get("dst", "default")
        if route.get("dev") in (interface, "lo") or destination == "default":
            continue
        if network.overlaps(ipaddress.IPv4Network(destination, strict=False)):
            raise ValueError(f"Gate subnet overlaps an existing route on {route.get('dev')}; no changes applied.")
