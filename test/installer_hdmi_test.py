import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


loader = importlib.machinery.SourceFileLoader(
    "installer_hdmi", str(Path(__file__).parent.parent / "scripts" / "gatekeeper-install"))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


class HdmiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "config.txt"
        self.cmdline = self.root / "cmdline.txt"
        self.config.write_text("[all]\ndtoverlay=vc4-kms-v3d\n")
        self.original = "console=serial0,115200 console=tty1 root=PARTUUID=test rootwait\n"
        self.cmdline.write_text(self.original)
        self.cmdline.chmod(0o644)

    def preview(self, profile="raspberry-pi-trixie-arm64"):
        return m.hdmi_preview(m.Inventory(), profile, "HDMI-A-2", "1280x720@60",
                              self.config, self.cmdline)

    def configure(self):
        return m.configure_hdmi(m.Inventory(), "raspberry-pi-trixie-arm64",
                                "HDMI-A-2", "1280x720@60", self.config, self.cmdline)

    def test_preserves_boot_arguments_and_adds_force_digital_mode(self):
        updated = m.hdmi_command_line(self.original, "HDMI-A-2", "1280x720@60")
        self.assertEqual(updated, self.original.rstrip() + " video=HDMI-A-2:1280x720@60D\n")
        self.assertEqual(m.hdmi_command_line(updated, "HDMI-A-2", "1280x720@60"), updated)

    def test_serial_console_preserved_and_tty1_added(self):
        updated = m.hdmi_command_line("console=serial0,115200 rootwait\n",
                                      "HDMI-A-1", "1920x1080@60")
        self.assertIn("console=serial0,115200", updated)
        self.assertIn("console=tty1", updated)
        self.assertIn("video=HDMI-A-1:1920x1080@60D", updated)

    def test_conflicts_and_malformed_input_rejected(self):
        for original in ("", "\n", "rootwait\nconsole=tty1\n", "nomodeset rootwait\n",
                         "video=HDMI-A-2:1920x1080@60D rootwait\n"):
            with self.subTest(original=original), self.assertRaises(ValueError):
                m.hdmi_command_line(original, "HDMI-A-2", "1280x720@60")
        for connector, mode in (("HDMI-A-3", "1280x720@60"),
                                 ("HDMI-A-2", "invalid")):
            with self.assertRaises(ValueError):
                m.hdmi_command_line(self.original, connector, mode)

    def test_other_port_settings_preserved(self):
        updated = m.hdmi_command_line("video=HDMI-A-1:1920x1080@60D rootwait\n",
                                      "HDMI-A-2", "1280x720@60")
        self.assertIn("video=HDMI-A-1:1920x1080@60D", updated)

    def test_preview_writes_nothing(self):
        report = self.preview()
        self.assertEqual(self.cmdline.read_text(), self.original)
        self.assertEqual(len(list(self.root.iterdir())), 2)
        self.assertFalse(report["hotplug_verified"])
        self.assertFalse(report["autologin_enabled"])

    def test_revpi_not_implicitly_reconfigured(self):
        with self.assertRaisesRegex(ValueError, "limited"):
            self.preview("revpi-trixie-arm64")

    def test_missing_kms_or_symlink_refused(self):
        self.config.write_text("#dtoverlay=vc4-kms-v3d\n")
        with self.assertRaisesRegex(ValueError, "overlay"):
            self.preview()
        self.config.unlink()
        target = self.root / "other-config"
        target.write_text("dtoverlay=vc4-kms-v3d\n")
        self.config.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.preview()

    def test_non_root_and_cancel_do_not_write(self):
        with patch.object(m.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(ValueError, "sudo"):
                self.configure()
        with patch.object(m.os, "geteuid", return_value=0), patch(
                "builtins.input", return_value="no"), patch("builtins.print"):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                self.configure()
        self.assertEqual(self.cmdline.read_text(), self.original)
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_confirmed_change_keeps_backup_and_file_permissions(self):
        with patch.object(m.os, "geteuid", return_value=0), patch(
                "builtins.input", return_value="HDMI-A-2"), patch("builtins.print"):
            result = self.configure()
            repeat = self.configure()
        self.assertEqual(Path(result["backup"]).read_text(), self.original)
        self.assertEqual(self.cmdline.stat().st_mode & 0o777, 0o644)
        self.assertEqual(repeat["mode"], "hdmi-already-configured")
        self.assertIsNone(repeat["backup"])
        self.assertEqual(len(list(self.root.glob("*.bak"))), 1)

    def test_failed_atomic_replace_preserves_original(self):
        with patch.object(m.os, "geteuid", return_value=0), patch(
                "builtins.input", return_value="HDMI-A-2"), patch("builtins.print"), patch.object(
                m.os, "replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                self.configure()
        self.assertEqual(self.cmdline.read_text(), self.original)
        self.assertEqual(len(list(self.root.glob("*.bak"))), 1)
        self.assertEqual(list(self.root.glob(".gatekeeper-hdmi-*")), [])

    def test_pi_profile_requires_model_and_vendor_packages(self):
        for model in ("Raspberry Pi 4 Model B Rev 1.5", "Raspberry Pi 5 Model B Rev 1.0"):
            inventory = m.Inventory()
            profile = m.platform_checks(
                inventory, {"ID": "debian", "VERSION_ID": "13"}, "arm64",
                {"raspberrypi-sys-mods": "1", "raspi-firmware": "1"}, [], model)
            self.assertEqual(profile, "raspberry-pi-trixie-arm64")
            self.assertFalse(any(c["status"] == "blocked" for c in inventory.checks))
        inventory = m.Inventory()
        profile = m.platform_checks(inventory, {"ID": "debian", "VERSION_ID": "13"},
                                    "arm64", {}, [], "Raspberry Pi 4 Model B Rev 1.5")
        self.assertEqual(profile, "unsupported")

    def test_configure_cli_requires_connector_and_interactive_output(self):
        for args in (["--configure-hdmi"], ["--configure-hdmi", "--json",
                                           "--hdmi-connector", "HDMI-A-2"]):
            with self.assertRaises(SystemExit) as result:
                m.main(args)
            self.assertEqual(result.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
