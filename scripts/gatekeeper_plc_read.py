"""Bounded read-only Modbus telemetry compatible with the existing gate program."""

import argparse
import ipaddress
import json
import secrets
import socket
import struct
import time


def request(transaction):
    return struct.pack(">HHHBBHH", transaction, 0, 6, 1, 1, 8, 8)


def decode(packet, transaction):
    if len(packet) < 8:
        raise ValueError("Incomplete Modbus response.")
    identifier, protocol, length, unit = struct.unpack(">HHHB", packet[:7])
    if identifier != transaction or protocol != 0 or unit != 1 or len(packet) != 6 + length:
        raise ValueError("Modbus response identity/framing mismatch.")
    if packet[7] == 0x81:
        if length != 3:
            raise ValueError("Malformed Modbus exception.")
        raise ValueError(f"Controller rejected FC1 telemetry read (exception {packet[8]}).")
    if length != 4 or packet[7:9] != b"\x01\x01":
        raise ValueError("Unexpected FC1 response function/byte count.")
    value = packet[9]
    return {
        "rawStatusByte": value,
        "physicalEstopHealthy": bool(value & 0x02),
        "localModeActive": bool(value & 0x04),
        "limitOpenActive": bool(value & 0x08),
        "limitCloseActive": bool(value & 0x10),
        "systemEnergized": not bool(value & 0x20),
        "remoteEnabled": bool(value & 0x80),
    }


def read(host, source, timeout=3):
    destination = ipaddress.IPv4Address(host)
    local = ipaddress.IPv4Address(source)
    network = ipaddress.IPv4Network(f"{source}/24", strict=False)
    if destination == local or any(
            address.is_multicast or address.is_loopback or address.is_unspecified or address.is_reserved
            or address.is_link_local or address in (network.network_address, network.broadcast_address)
            for address in (destination, local)) or destination not in network:
        raise ValueError("Use distinct usable gate/source IPv4 addresses on the same /24.")
    transaction = secrets.randbelow(65536)
    started = time.monotonic()
    deadline = started + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.settimeout(timeout)
        stream.bind((str(local), 0))
        stream.connect((str(destination), 502))
        stream.sendall(request(transaction))

        def receive(length):
            data = bytearray()
            while len(data) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Controller telemetry read deadline exceeded.")
                stream.settimeout(remaining)
                chunk = stream.recv(length - len(data))
                if not chunk:
                    raise ValueError("Controller closed an incomplete telemetry response.")
                data.extend(chunk)
            return bytes(data)

        header = receive(7)
        length = struct.unpack(">H", header[4:6])[0]
        if length not in (3, 4):
            raise ValueError("Unexpected bounded Modbus response length.")
        result = decode(header + receive(length - 1), transaction)
    return {
        "ip": str(destination), "sourceIp": str(local), "port": 502,
        "protocol": "Modbus TCP FC1", "readOnly": True,
        "identityVerified": False,
        "sampledAt": int(time.time() * 1000),
        "rttMs": round((time.monotonic() - started) * 1000, 1),
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(read(args.host, args.source)))
    except (ValueError, OSError) as error:
        parser.exit(1, f"ERROR: Read-only controller probe failed: {error}\n")


if __name__ == "__main__":
    main()
