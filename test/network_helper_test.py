import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


loader = importlib.machinery.SourceFileLoader(
    "network", str(Path(__file__).parent.parent / "scripts" / "revpi-network"))
spec = importlib.util.spec_from_loader(loader.name, loader)
network = importlib.util.module_from_spec(spec)
loader.exec_module(network)
CONFIG = dict(interface="lan1", masterMode="server", clientMode="dhcp",
              gateway="10.10.10.2", ipStart="10.10.10.50", ipEnd="10.10.10.150",
              staticIp="10.10.10.2")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        mapping = patch.object(network.interfaces, "read_mapping", return_value=None)
        mapping.start()
        self.addCleanup(mapping.stop)

    def test_all_modes_and_legacy_alias(self):
        for mode in ("server", "static", "dhcp"):
            config = dict(CONFIG, masterMode="server" if mode == "server" else "client",
                          clientMode="static" if mode == "server" else mode)
            self.assertEqual(network.validate(config), mode)
        self.assertEqual(network.validate(dict(CONFIG, interface="eth1")), "server")

    def test_reject_invalid_subnets_and_management_port(self):
        for fields in (dict(interface="lan0"), dict(interface="eth0"),
                       dict(gateway="10.10.10.100"), dict(ipStart="10.10.11.50"),
                       dict(ipStart="10.10.10.151"), dict(gateway="10.10.10.0"),
                       dict(ipEnd="10.10.10.255"), dict(gateway="127.0.0.1"),
                       dict(staticIp="bad", masterMode="client", clientMode="static")):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                network.validate(dict(CONFIG, **fields))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        conf = patch.object(network, "CONF", Path(self.temp.name) / "ot-network.conf")
        conf.start()
        self.addCleanup(conf.stop)
        role = patch.object(network.interfaces, "read_mapping", return_value=None)
        role.start()
        self.addCleanup(role.stop)
        protect = patch.object(network.interfaces, "ensure_gate_not_management")
        protect.start()
        self.addCleanup(protect.stop)
        subnet = patch.object(network.interfaces, "ensure_gate_subnet")
        subnet.start()
        self.addCleanup(subnet.stop)
        exists = Path.exists

        def fake_exists(path):
            if str(path) in ("/sys/class/net/lan1", "/usr/sbin/dnsmasq"):
                return True
            return exists(path)

        patcher = patch.object(Path, "exists", fake_exists)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []
        self.fail_activation = False
        self.fail_service = False
        self.changed = False

        def run(*args):
            self.calls.append(args)
            if args[:4] == ("nmcli", "-g", "GENERAL.CON-UUID", "device"):
                return "profile" if self.changed else "--"
            if args == ("nmcli", "-g", "UUID", "connection", "show"):
                return "profile"
            if args[:2] == ("nmcli", "-g"):
                return {"connection.interface-name": "lan1", "ipv4.method": "manual",
                        "ipv4.addresses": "10.10.10.1/24", "ipv4.gateway": "",
                        "ipv4.never-default": "no", "ipv4.ignore-auto-dns": "no",
                        "connection.autoconnect": "yes"}[args[2]]
            if args[:3] == ("nmcli", "--wait", "35"):
                if self.fail_activation:
                    raise RuntimeError("IP address already in use")
                self.changed = True
            if args[:3] == ("systemctl", "start", "dnsmasq") and self.fail_service:
                raise RuntimeError("DHCP service failed")
            if args[:3] == ("ip", "-j", "-4"):
                return json.dumps([{"addr_info": [{"family": "inet", "local": "10.10.10.2"}]}])
            return ""

        mock_run = patch.object(network, "run", run)
        mock_run.start()
        self.addCleanup(mock_run.stop)
        service = patch.object(network.subprocess, "run", return_value=Mock(returncode=3))
        service.start()
        self.addCleanup(service.stop)

    def test_server_uses_persistent_profile_and_actual_interface(self):
        result = network.apply(CONFIG)
        self.assertTrue(result["ok"])
        self.assertEqual(result["config"]["interface"], "lan1")
        text = network.CONF.read_text()
        self.assertIn("interface=lan1\n", text)
        self.assertIn("port=0\n", text)
        self.assertIn("dhcp-option=3\n", text)
        self.assertTrue(any(call[:3] == ("nmcli", "connection", "modify") for call in self.calls))
        self.assertFalse(any("lan0" in call or "dhclient" in call for call in self.calls))

    def test_dhcp_client_uses_networkmanager_not_dhclient(self):
        network.apply(dict(CONFIG, masterMode="client", clientMode="dhcp"))
        changes = next(call for call in self.calls if call[:3] == ("nmcli", "connection", "modify"))
        self.assertEqual(changes[changes.index("ipv4.method") + 1], "auto")
        self.assertEqual(changes[changes.index("ipv4.addresses") + 1], "")
        self.assertFalse(network.CONF.exists())

    def test_static_client_removes_only_managed_dhcp_config(self):
        network.CONF.write_text("previous DHCP config")
        network.apply(dict(CONFIG, masterMode="client", clientMode="static"))
        self.assertFalse(network.CONF.exists())
        self.assertIn(("systemctl", "stop", "dnsmasq"), self.calls)

    def test_duplicate_address_restores_previous_profile_and_dhcp_file(self):
        self.fail_activation = True
        network.CONF.write_text("previous DHCP config")
        with self.assertRaisesRegex(RuntimeError, "already in use.*previous settings restored"):
            network.apply(CONFIG)
        self.assertEqual(network.CONF.read_text(), "previous DHCP config")
        modifications = [call for call in self.calls if call[:3] == ("nmcli", "connection", "modify")]
        self.assertEqual(len(modifications), 2)
        self.assertIn("10.10.10.1/24", modifications[-1])

    def test_failed_service_does_not_leave_new_address_active(self):
        self.fail_service = True
        with self.assertRaisesRegex(RuntimeError, "DHCP service failed"):
            network.apply(CONFIG)
        self.assertIn(("nmcli", "connection", "down", "uuid", "profile"), self.calls)
        self.assertFalse(network.CONF.exists())

    def test_read_only_service_directory_fails_before_network_changes(self):
        with patch.object(network.tempfile, "TemporaryFile", side_effect=OSError("read-only")):
            with self.assertRaisesRegex(RuntimeError, "systemd drop-in"):
                network.apply(CONFIG)
        self.assertFalse(any(call[:3] == ("nmcli", "connection", "modify") for call in self.calls))
        self.assertNotIn(("systemctl", "stop", "dnsmasq"), self.calls)

    def test_collision_probe_failure_precedes_mutations(self):
        original = network.run
        def fail_probe(*args):
            if args[0] == "arping":
                raise RuntimeError("ARP address conflict or probe failure")
            return original(*args)
        with patch.object(network, "run", side_effect=fail_probe):
            with self.assertRaisesRegex(RuntimeError, "ARP address conflict"):
                network.apply(dict(CONFIG, masterMode="client", clientMode="static"))
        self.assertFalse(any(call[0] in ("nmcli", "systemctl") for call in self.calls))

    def test_mapped_profile_is_pinned_to_mac_without_name_binding(self):
        mapping = {"version": 1, "roles": {
            "management": {"mac": "02:11:22:33:44:55", "driver": "bcmgenet"},
            "gate": {"mac": "02:11:22:33:44:66", "driver": "r8152"}}}
        original = network.run
        def mapped_run(*args):
            if args[:3] == ("nmcli", "-g", "802-3-ethernet.mac-address"):
                return ""
            return original(*args)
        with patch.object(network.interfaces, "read_mapping", return_value=mapping), \
                patch.object(network.interfaces, "resolve", return_value="lan1"), \
                patch.object(network, "run", side_effect=mapped_run):
            network.apply(dict(CONFIG, masterMode="client", clientMode="static"))
        modifications = [call for call in self.calls if call[:3] == ("nmcli", "connection", "modify")]
        self.assertIn("802-3-ethernet.mac-address", modifications[0])
        self.assertIn(mapping["roles"]["gate"]["mac"], modifications[0])
        self.assertEqual(modifications[0][modifications[0].index("connection.interface-name") + 1], "")

    def test_inactive_mac_profile_is_reused_with_nmcli_escaped_mac(self):
        mapping = {"version": 1, "roles": {
            "management": {"mac": "02:11:22:33:44:55", "driver": "bcmgenet"},
            "gate": {"mac": "02:11:22:33:44:66", "driver": "r8152"}}}
        original = network.run
        def mapped_run(*args):
            if args[:3] == ("nmcli", "-g", "802-3-ethernet.mac-address"):
                return "02\\:11\\:22\\:33\\:44\\:66"
            if args[:3] == ("nmcli", "-g", "connection.interface-name"):
                return ""
            return original(*args)
        with patch.object(network.interfaces, "read_mapping", return_value=mapping), \
                patch.object(network.interfaces, "resolve", return_value="lan1"), \
                patch.object(network, "run", side_effect=mapped_run):
            result = network.apply(dict(CONFIG, masterMode="client", clientMode="static"))
        self.assertEqual(result["profile"], "profile")
        self.assertFalse(any(call[:3] == ("nmcli", "connection", "add") for call in self.calls))


if __name__ == "__main__":
    unittest.main()
