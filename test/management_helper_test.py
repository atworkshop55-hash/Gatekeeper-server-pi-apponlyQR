import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


loader = importlib.machinery.SourceFileLoader(
    "management", str(Path(__file__).parent.parent / "scripts" / "revpi-management"))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


class ManagementTests(unittest.TestCase):
    def setUp(self):
        patch.object(m.interfaces, "read_mapping", return_value=None).start()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for key, value in [("ROOT", root), ("STATE", root / "state.json"),
                           ("STATUS", root / "status.json"), ("LOCK", root / "lock")]:
            p = patch.object(m, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.run = patch.object(m, "run", return_value="profile").start()
        self.addCleanup(patch.stopall)
        self.boot = patch.object(m, "boot_id", return_value="boot").start()
        self.link = patch.object(m, "link_up", return_value=True).start()
        self.addresses = patch.object(m, "addresses", return_value=[]).start()

    def state(self, **fields):
        return {"config": dict(m.DEFAULT), "interface": "lan0",
                "profile": "profile", "events": [], **fields}

    def test_default_and_validation(self):
        executable = Path(__file__).parent.parent / "scripts" / "revpi-management"
        self.assertEqual(executable.read_bytes().split(b"\n")[0], b"#!/usr/bin/python3")
        self.assertEqual(m.validate(dict(m.DEFAULT))["mode"], "dhcp")
        self.assertEqual(m.validate(dict(m.DEFAULT))["fallback"], "192.168.250.1/24")
        for value in ["192.168.250.0/24", "192.168.250.255/24",
                      "127.0.0.1/24", "224.1.1.1/24", "0.1.2.3/24",
                      "169.254.1.1/24", "10.1.2.3", "192.168.1.1/31"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.validate(dict(m.DEFAULT, fallback=value))
        for iface in ["lan1", "eth1", "wlan0", "lan0; reboot"]:
            with self.subTest(interface=iface), self.assertRaises(ValueError):
                m.validate(dict(m.DEFAULT, interface=iface))

    def test_static_prefix_gateway_dns(self):
        valid = dict(m.DEFAULT, mode="static", address="192.168.30.5/24",
                     gateway="192.168.30.1", dns=["1.1.1.1", "8.8.8.8"])
        self.assertEqual(m.validate(valid)["dns"], valid["dns"])
        for fields in [{"gateway": "192.168.40.1"}, {"gateway": "192.168.30.5"},
                       {"gateway": "192.168.30.0"}, {"dns": ["bad"]},
                       {"dns": "1.1.1.1"}, {"dns": ["1.1.1.1"] * 4}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                m.validate(dict(valid, **fields))

    def test_gate_overlap_is_rejected(self):
        self.addresses.return_value = [{"ifname": "lan1", "addr_info": [
            {"local": "10.10.10.2", "prefixlen": 24}]}]
        with self.assertRaisesRegex(ValueError, "overlaps lan1"):
            m.no_overlap("10.10.10.1/24", "lan0")
        m.no_overlap("192.168.250.1/24", "lan0")

    def test_fallback_exactly_at_sixty_seconds_and_no_dhcp_server(self):
        state = self.state()
        self.assertEqual(m.monitor_step(state, 59.999, 0)[0], "waiting-dhcp")
        self.assertNotIn("addedFallback", state)
        self.assertEqual(m.monitor_step(state, 60, 0)[0], "fallback")
        self.assertEqual(state["addedFallback"], "192.168.250.1/24")
        self.assertEqual(len(state["events"]), 1)
        commands = [call.args for call in self.run.call_args_list]
        self.assertIn(("ip", "addr", "add", "192.168.250.1/24", "dev", "lan0"), commands)
        self.assertFalse(any("dnsmasq" in cmd for cmd in commands))
        self.addresses.return_value = [{"ifname": "lan0", "addr_info": [
            {"local": "192.168.250.1", "prefixlen": 24}]}]
        self.assertEqual(m.monitor_step(state, 65, 0)[0], "fallback")
        self.assertEqual(len(state["events"]), 1)

    def test_late_dhcp_removes_fallback_without_stopping_dhcp(self):
        state = self.state(addedFallback="192.168.250.1/24")
        self.addresses.return_value = [{"ifname": "lan0", "addr_info": [
            {"local": "192.168.250.1", "prefixlen": 24},
            {"local": "192.168.101.151", "prefixlen": 24, "dynamic": True}]}]
        mode, waiting_since, ips = m.monitor_step(state, 75, 0)
        self.assertEqual((mode, waiting_since, ips), ("dhcp", 75, ["192.168.101.151"]))
        self.assertNotIn("addedFallback", state)
        self.run.assert_any_call("ip", "addr", "del", "192.168.250.1/24", "dev", "lan0")

    def test_lease_loss_starts_new_sixty_second_wait(self):
        state = self.state()
        self.addresses.return_value = [{"ifname": "lan0", "addr_info": [
            {"local": "192.168.101.151", "prefixlen": 24, "dynamic": True}]}]
        _, wait, _ = m.monitor_step(state, 200, 0)
        self.addresses.return_value = []
        self.assertEqual(m.monitor_step(state, 259.9, wait)[0], "waiting-dhcp")
        self.assertEqual(m.monitor_step(state, 260, wait)[0], "fallback")

    def test_no_fallback_when_cable_unplugged_or_address_collides(self):
        self.link.return_value = False
        self.assertEqual(m.monitor_step(self.state(), 60, 0)[0], "waiting-link")
        self.link.return_value = True
        with patch.object(m, "collision_check", side_effect=RuntimeError("conflict")):
            with self.assertRaisesRegex(RuntimeError, "conflict"):
                m.monitor_step(self.state(), 60, 0)
        self.assertFalse(any(call.args[:3] == ("ip", "addr", "add") for call in self.run.call_args_list))

    def test_recovery_refuses_external_profile_change(self):
        self.run.return_value = "different-profile"
        with self.assertRaisesRegex(RuntimeError, "changed externally"):
            m.monitor_step(self.state(), 60, 0)

    def test_rollback_restores_entire_original_profile_at_deadline(self):
        state = self.state(pending={"id": "test", "deadline": 100, "boot": "boot",
                                   "previous": {"ipv4.method": "manual"}, "config": None})
        with patch.object(m.time, "time", return_value=100), patch.object(m, "activate") as activate:
            self.assertEqual(m.monitor_step(state, 60, 0)[0], "unmanaged")
            activate.assert_called_once_with("profile", {"ipv4.method": "manual"}, "lan0")
        self.assertIsNone(m.read(m.STATE)["pending"])
        self.assertIsNone(state["config"])

    def test_reboot_rolls_back_unconfirmed_change_even_before_deadline(self):
        state = self.state(pending={"id": "test", "deadline": 99999999999, "boot": "old-boot",
                                   "previous": {}, "config": None})
        with patch.object(m, "activate") as activate:
            self.assertEqual(m.monitor_step(state, 1, 0)[0], "unmanaged")
            activate.assert_called_once()

    def test_confirm_requires_matching_revision_fresh_status_and_live_address(self):
        state = self.state(pending={"id": "test", "deadline": 200, "boot": "boot",
                                   "previous": {}, "config": None})
        m.write(m.STATE, state)
        with patch.object(m.time, "time", return_value=100):
            with self.assertRaises(ValueError):
                m.confirm("wrong")
            for status in [
                {"sampledAt": 100000, "pendingId": "test", "state": "waiting-dhcp"},
                {"sampledAt": 80000, "pendingId": "test", "state": "dhcp"},
                {"sampledAt": 100000, "pendingId": "old", "state": "dhcp"},
                {"sampledAt": 100000, "pendingId": "test", "state": "error", "error": "conflict"},
            ]:
                m.write(m.STATUS, status)
                with self.assertRaises(RuntimeError):
                    m.confirm("test")
            m.write(m.STATUS, {"sampledAt": 100000, "pendingId": "test", "state": "dhcp"})
            self.assertTrue(m.confirm("test")["ok"])
            self.assertIsNone(m.read(m.STATE)["pending"])

    def test_apply_requires_monitor_and_saves_recovery_before_activation(self):
        self.run.return_value = ""
        config = dict(m.DEFAULT)
        with patch.object(m, "interface", return_value="lan0"), \
                patch.object(m, "profile", return_value="profile"), \
                patch.object(m.Path, "exists", return_value=True), \
                patch.object(m, "read", return_value=None), \
                patch.object(m, "remove_fallback"), \
                patch.object(m, "activate") as activate:
            # Inspect the on-disk recovery snapshot using the real reader.
            real_read = lambda path: __import__("json").loads(path.read_text())
            def inspect(profile_id, settings, iface):
                saved = real_read(m.STATE)
                self.assertEqual(saved["pending"]["previous"]["ipv4.method"], "")
                self.assertEqual(settings["ipv4.method"], "auto")
                self.assertEqual(settings["ipv4.addresses"], "")
                self.assertEqual(settings["connection.interface-name"], "lan0")
            activate.side_effect = inspect
            result = m.apply(config)
            self.assertTrue(result["ok"])
            self.assertIn("pendingId", result)
            self.run.assert_any_call("systemctl", "is-active", "--quiet", "gatekeeper-management.service")

    def test_atomic_state_permissions_and_no_leftover_temporary_files(self):
        m.write(m.STATE, self.state())
        m.write(m.STATUS, {"installed": True})
        self.assertEqual(m.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(m.STATUS.stat().st_mode & 0o777, 0o644)
        self.assertEqual({p.name for p in m.ROOT.iterdir()}, {"state.json", "status.json"})

    def test_existing_dhcp_can_be_adopted_without_reactivation(self):
        def commands(*args):
            if args[:4] == ("nmcli", "-g", "ipv4.method", "connection"):
                return "auto"
            if args[:3] in [
                ("nmcli", "-g", "ipv4.never-default"),
                ("nmcli", "-g", "ipv4.ignore-auto-dns")
            ]:
                return "no"
            return ""
        self.run.side_effect = commands
        self.addresses.return_value = [{"ifname": "lan0", "addr_info": [
            {"local": "192.168.101.151", "prefixlen": 24, "dynamic": True}]}]
        with patch.object(m, "interface", return_value="lan0"), \
                patch.object(m, "profile", return_value="profile"):
            self.assertTrue(m.adopt()["ok"])
        self.assertEqual(m.read(m.STATE)["config"], m.DEFAULT)
        self.assertFalse(any("up" in call.args for call in self.run.call_args_list))
        self.assertFalse(any(call.args[0] == "ip" for call in self.run.call_args_list))

    def test_non_dhcp_profile_is_not_silently_adopted(self):
        self.run.return_value = "manual"
        with patch.object(m, "interface", return_value="lan0"), \
                patch.object(m, "profile", return_value="profile"):
            with self.assertRaisesRegex(RuntimeError, "existing DHCP"):
                m.adopt()
        self.assertFalse(m.STATE.exists())

    def test_fallback_address_obtained_from_dhcp_is_not_removed(self):
        state = self.state(addedFallback="192.168.250.1/24")
        self.addresses.return_value = [{"ifname": "lan0", "addr_info": [
            {"local": "192.168.250.1", "prefixlen": 24, "dynamic": True}]}]
        self.assertEqual(m.monitor_step(state, 75, 0)[0], "dhcp")
        self.assertNotIn("addedFallback", state)
        self.assertFalse(any(call.args[:3] == ("ip", "addr", "del") for call in self.run.call_args_list))


if __name__ == "__main__":
    unittest.main()
