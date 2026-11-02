import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import gatekeeper_interfaces as interfaces


DEVICES = [{"name": "eth0", "mac": "02:11:22:33:44:55", "driver": "bcmgenet"},
           {"name": "enx021122334466", "mac": "02:11:22:33:44:66", "driver": "r8152"}]
VALUE = {"version": 1, "roles": {
    "management": {"mac": DEVICES[0]["mac"], "driver": "bcmgenet"},
    "gate": {"mac": DEVICES[1]["mac"], "driver": "r8152"}}}


class InterfaceTests(unittest.TestCase):
    def test_resolves_names_from_identity_not_enumeration_or_usb_socket(self):
        devices = [dict(DEVICES[1], name="eth0"), dict(DEVICES[0], name="eth1")]
        self.assertEqual(interfaces.resolved_roles(VALUE, devices), {"management": "eth1", "gate": "eth0"})
        self.assertEqual(interfaces.resolved_roles(VALUE, list(reversed(DEVICES)))["gate"], "enx021122334466")

    def test_missing_replaced_duplicate_or_wrong_driver_fails_closed(self):
        cases = [DEVICES[:1], [DEVICES[0], dict(DEVICES[1], mac="02:00:00:00:00:99")],
                 DEVICES + [dict(DEVICES[1], name="eth2")],
                 [DEVICES[0], dict(DEVICES[1], driver="other")]]
        for devices in cases:
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                interfaces.resolved_roles(VALUE, devices)

    def test_distinct_valid_roles_required(self):
        for value in ({}, {"version": 2}, {"version": 1, "roles": {}},
                      {"version": 1, "roles": {"management": VALUE["roles"]["management"],
                                              "gate": VALUE["roles"]["management"]}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                interfaces.validate_mapping(value)

    def test_request_scope_and_legacy_compatibility(self):
        with patch.object(interfaces, "read_mapping", return_value=None):
            interfaces.validate_request("gate", "lan1")
            interfaces.validate_request("management", "eth0")
            for role, requested in (("gate", "eth0"), ("management", "lan1"), ("gate", "wlan0")):
                with self.assertRaises(ValueError):
                    interfaces.validate_request(role, requested)
        with patch.object(interfaces, "read_mapping", return_value=VALUE), patch.object(
                interfaces, "inventory", return_value=DEVICES):
            interfaces.validate_request("gate", DEVICES[1]["name"])
            self.assertEqual(interfaces.resolve("gate"), DEVICES[1]["name"])
            with self.assertRaises(ValueError):
                interfaces.validate_request("management", DEVICES[1]["name"])

    def test_existing_bad_mapping_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                interfaces, "MAPPING", Path(temporary) / "roles.json"):
            interfaces.MAPPING.write_text(json.dumps(VALUE))
            interfaces.MAPPING.chmod(0o644)
            with self.assertRaises(ValueError):
                interfaces.resolve("gate")
            interfaces.MAPPING.unlink()
            interfaces.MAPPING.symlink_to(Path(temporary) / "absent")
            with self.assertRaises(ValueError):
                interfaces.resolve("gate")

    def test_gate_default_route_is_rejected(self):
        with patch.object(interfaces.subprocess, "run", return_value=Mock(
                stdout='[{"dst":"default","dev":"eth1"}]')):
            with self.assertRaises(ValueError):
                interfaces.ensure_gate_not_management("eth1")
            interfaces.ensure_gate_not_management("eth0")

    def test_gate_subnet_overlap_is_rejected_but_own_link_allowed(self):
        with patch.object(interfaces.subprocess, "run", return_value=Mock(
                stdout='[{"dst":"192.168.101.0/24","dev":"eth0"},{"dst":"10.10.10.0/24","dev":"eth1"}]')):
            interfaces.ensure_gate_subnet("eth1", "10.10.10.3")
            with self.assertRaises(ValueError):
                interfaces.ensure_gate_subnet("eth1", "192.168.101.3")

    def test_physical_inventory_excludes_wifi_and_virtual_interfaces(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(interfaces, "NET", Path(temporary)):
            for name in ("eth0", "wlan0", "veth0"):
                path = interfaces.NET / name
                path.mkdir()
                if name != "veth0":
                    (path / "device").mkdir()
                if name == "wlan0":
                    (path / "wireless").mkdir()
                if name == "eth0":
                    (path / "type").write_text("1")
                    (path / "address").write_text(DEVICES[0]["mac"])
                    (path / "device" / "bcmgenet").mkdir()
                    (path / "device" / "driver").symlink_to(path / "device" / "bcmgenet")
            self.assertEqual(interfaces.inventory(), [DEVICES[0]])

    def test_helper_uses_mapped_gate_and_never_activates_a_default_route_port(self):
        import importlib.machinery
        import importlib.util
        source = Path(__file__).parent.parent / "scripts" / "revpi-network"
        loader = importlib.machinery.SourceFileLoader("mapped_gate_network", str(source))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        network = importlib.util.module_from_spec(spec)
        loader.exec_module(network)
        config = {"interface": "lan1", "masterMode": "client", "clientMode": "dhcp"}
        with patch.object(interfaces, "read_mapping", return_value=VALUE), patch.object(
                interfaces, "inventory", return_value=DEVICES), patch.object(
                interfaces, "ensure_gate_not_management", side_effect=ValueError("default route")) as guard, \
                patch.object(network, "run") as run:
            with self.assertRaisesRegex(ValueError, "default route"):
                network.apply(config)
            guard.assert_called_once_with(DEVICES[1]["name"])
            run.assert_not_called()
