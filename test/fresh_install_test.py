import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from server_source_test import installer


class FreshInstallTests(unittest.TestCase):
    def test_plan_is_read_only_even_on_an_existing_server(self):
        with patch.object(sys, "argv", ["install-server", "--plan", "--host", "test-server.local"]), \
                patch.object(installer, "require_fresh") as fresh, \
                patch.object(installer, "run") as run, \
                patch.object(installer, "protect_copy") as copy, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(installer.main(), 0)
        self.assertIn("backend STOPPED", output.getvalue())
        fresh.assert_not_called()
        run.assert_not_called()
        copy.assert_not_called()

    def test_noninteractive_install_does_not_run_mutating_commands(self):
        with patch.object(installer.sys, "platform", "linux"), \
                patch.object(installer.os, "geteuid", return_value=0), \
                patch.object(installer.sys.stdin, "isatty", return_value=False), \
                patch.object(installer, "run") as run:
            with self.assertRaisesRegex(ValueError, "interactive"):
                installer.require_fresh("server.local")
            run.assert_not_called()

    def test_copy_excludes_git_and_bytecode_and_records_exact_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "protected"
            source.mkdir()
            destination.mkdir()
            for folder in ("scripts", "deployment", "docs", ".git"):
                (source / folder).mkdir()
            (source / "install.sh").write_text("#!/bin/sh\n")
            (source / "scripts/tool").write_text("tool\n")
            (source / "deployment/flows.json").write_text("[]\n")
            (source / "docs/INSTALL.md").write_text("instructions\n")
            (source / ".git/config").write_text("not copied")
            (source / "scripts/__pycache__").mkdir()
            (source / "scripts/__pycache__/tool.pyc").write_bytes(b"cache")
            previous = os.umask(0o002)
            try:
                with patch.object(installer.tempfile, "mkdtemp", return_value=str(destination)):
                    self.assertEqual(installer.protect_copy(source), destination)
            finally:
                os.umask(previous)
            hashes = json.loads((destination / "source-manifest.json").read_text())
            self.assertEqual(set(hashes), {
                "install.sh", "scripts/tool", "deployment/flows.json", "docs/INSTALL.md"})
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "scripts/__pycache__").exists())
            for path in destination.rglob("*"):
                self.assertEqual(path.stat().st_mode & 0o022, 0)

    def test_copy_refuses_symlink_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "protected"
            source.mkdir()
            destination.mkdir()
            for folder in ("scripts", "deployment", "docs"):
                (source / folder).mkdir()
            (source / "install.sh").symlink_to("/etc/passwd")
            with patch.object(installer.tempfile, "mkdtemp", return_value=str(destination)), \
                    self.assertRaisesRegex(ValueError, "Unsafe installer"):
                installer.protect_copy(source)

    def test_platform_directories_remain_readable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("opt", "etc", "etc/systemd/system", "var/lib"):
                (root / name).mkdir(parents=True, exist_ok=True)

            def mapped(value):
                path = Path(value)
                return path if path.is_relative_to(root) else root / str(path).lstrip("/")

            previous = os.umask(0o077)
            try:
                with patch.object(installer, "Path", side_effect=mapped), \
                        patch.object(installer.os, "fchown"), patch.object(installer, "run") as run:
                    installer.install_platform()
            finally:
                os.umask(previous)
            for name in ("var/lib/gatekeeper-management", "var/lib/misc", "opt/gatekeeper-network",
                         "opt/gatekeeper-server-tools"):
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o755)
            self.assertIn("--reference-app", (
                root / "etc/systemd/system/gatekeeper-network-static.service.d/95-reference-app.conf").read_text())
            run.assert_any_call("systemctl", "enable", "--now", "gatekeeper-network-static.service",
                                "gatekeeper-reference-management.service")


if __name__ == "__main__":
    unittest.main()
