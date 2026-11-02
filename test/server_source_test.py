import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import server_source as source

loader = importlib.machinery.SourceFileLoader("fresh_installer_test", str(ROOT / "scripts/install-server"))
spec = importlib.util.spec_from_loader(loader.name, loader)
installer = importlib.util.module_from_spec(spec)
loader.exec_module(installer)


class ServerSourceTests(unittest.TestCase):
    def test_selected_reference_controls_are_preserved_and_hosts_are_complete(self):
        baseline = json.loads((ROOT / "deployment/reference-flows.json").read_text())
        before = {node["id"]: node for node in baseline}
        result = source.render("other-gatekeeper.local")
        nodes = source.validate_flows(result)
        for identifier in source.CONTROL_IDS:
            self.assertEqual(nodes[identifier], before[identifier])
        self.assertEqual(nodes["ade6a257a7680761"], before["ade6a257a7680761"])
        self.assertEqual(nodes["tablet_dynsec_broker"]["broker"], "other-gatekeeper.local")
        self.assertEqual(nodes["tablet_dynsec_tls"]["servername"], "other-gatekeeper.local")
        self.assertIn("global.set('commissioning_host',\"other-gatekeeper.local\");",
                      nodes["commissioning_socket"]["initialize"])
        self.assertNotIn("gatekeeper-nexus.local", json.dumps(result))
        self.assertFalse(any(node.get("credentials") for node in result))
        self.assertNotIn("control_commissioning", nodes)
        self.assertNotIn("demo_plc_sample", nodes)

    def test_invalid_host_graph_and_credentials_fail(self):
        for host in ("127.0.0.1", "https://server.local", "bad';reboot.local", "", "localhost"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                source.render(host)
        baseline = source.render("server.local")
        for mutate in (
                lambda value: value.append(copy.deepcopy(value[0])),
                lambda value: value[0].update(credentials={"password": "forbidden"}),
                lambda value: value[0].update(type="ui_template"),
                lambda value: value[0].update(wires=[["missing"]])):
            flows = copy.deepcopy(baseline)
            mutate(flows)
            with self.assertRaises(ValueError):
                source.validate_flows(flows)

    def test_generated_file_ignores_group_writable_umask_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows.json"
            previous = os.umask(0o002)
            try:
                source.render_file(path, "server.local")
            finally:
                os.umask(previous)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            with self.assertRaises(FileExistsError):
                source.render_file(path, "server.local")

    def test_existing_install_refused_before_any_mutating_command(self):
        with patch.object(installer.sys, "platform", "linux"), \
                patch.object(installer.os, "geteuid", return_value=0), \
                patch.object(installer.sys.stdin, "isatty", return_value=True), \
                patch.object(installer.sys.stdout, "isatty", return_value=True), \
                patch.object(installer.Path, "read_text", side_effect=[
                    "Raspberry Pi 4 Model B", 'ID=debian\nVERSION_ID="13"\n']), \
                patch.object(installer.Path, "exists", side_effect=[False, False, True]), \
                patch.object(installer, "run", return_value="arm64") as run:
            with self.assertRaisesRegex(ValueError, "Existing/partial"):
                installer.require_fresh("server.local")
            run.assert_called_once_with("dpkg", "--print-architecture", capture=True)

    def test_credentials_are_generated_locally_and_never_part_of_shell_arguments(self):
        code = installer.encrypt_credentials()
        self.assertIn("flag:'wx'", code)
        self.assertIn("/etc/gatekeeper/manager-password", code)
        self.assertIn("aes-256-ctr", code)
        self.assertNotIn("console.log", code)


if __name__ == "__main__":
    unittest.main()
