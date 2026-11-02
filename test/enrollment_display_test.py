import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import enrollment_display as display

loader = importlib.machinery.SourceFileLoader(
    "enrollment_tool_test", str(Path(__file__).parent.parent / "scripts/gatekeeper-enroll"))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class TerminalOutput(io.StringIO):
    def isatty(self):
        return True


class EnrollmentDisplayTests(unittest.TestCase):
    def test_policy_requires_root_owned_nonwritable_regular_file(self):
        policy = MagicMock()
        policy.is_symlink.return_value = False
        policy.is_file.return_value = True
        policy.read_text.return_value = "enabled\n"
        with patch.object(display, "POLICY", policy):
            for uid, mode in ((1000, 0o600), (0, 0o622)):
                policy.lstat.return_value.st_uid = uid
                policy.lstat.return_value.st_mode = mode
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    display.require_enabled()
            policy.lstat.return_value.st_uid = 0
            policy.lstat.return_value.st_mode = 0o600
            display.require_enabled()
            policy.read_text.return_value = "disabled\n"
            with self.assertRaisesRegex(ValueError, "not enabled"):
                display.require_enabled()
            policy.is_symlink.return_value = True
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                display.require_enabled()

    def test_headless_failure_does_not_open_console(self):
        with patch.object(display, "monitor_connected", return_value=False), \
                patch.object(display.os, "open") as opened:
            with self.assertRaisesRegex(ValueError, "No connected"):
                with display.PhysicalDisplay():
                    self.fail("Headless display accepted")
        opened.assert_not_called()

    def test_virtual_console_is_restored_and_handles_closed(self):
        calls = []

        def ioctl(fd, action, argument):
            calls.append((fd, action, argument))
            if action == 0x5600:
                return struct.pack("i", 9)
            if action == 0x5603:
                return struct.pack("HHH", 1 if len(calls) < 4 else 9, 0, 0)
            if action == 0x4B3B:
                return struct.pack("i", 0)
            return 0

        with patch.object(display, "monitor_connected", return_value=True), \
                patch.object(display.os, "open", side_effect=[10, 11]), \
                patch.object(display.os, "close") as close, \
                patch.object(display.fcntl, "ioctl", side_effect=ioctl), \
                patch.object(display.termios, "tcgetattr", return_value=[]), \
                patch.object(display.termios, "tcsetattr"), patch.object(display.tty, "setcbreak"):
            with display.PhysicalDisplay() as screen:
                self.assertEqual(screen.fd, 11)
                self.assertEqual(screen.number, 9)
            self.assertIsNone(screen.control)
        self.assertIn((11, 0x4B3A, 1), calls)
        self.assertIn((11, 0x4B3A, 0), calls)
        self.assertIn((10, 0x5606, 1), calls)
        self.assertFalse(any(action == 0x5608 for _, action, _ in calls))
        self.assertEqual([call.args[0] for call in close.call_args_list], [11, 10])

    def test_display_loss_and_console_switch_fail_explicitly(self):
        screen = display.PhysicalDisplay()
        screen.number = 9
        for connected, active in ((False, 9), (True, 1)):
            with patch.object(display, "monitor_connected", return_value=connected), \
                    patch.object(screen, "active", return_value=active):
                with self.assertRaisesRegex(ValueError, "cancelling"):
                    screen.check()

    def test_ssh_supervision_has_hard_timeout_and_no_qr_payload(self):
        process = MagicMock()
        process.poll.side_effect = [None, 0, 0]
        process.returncode = 0
        with patch.object(display, "require_enabled"), \
                patch.object(display, "monitor_connected", return_value=True), \
                patch.object(display.sys.stdin, "isatty", return_value=True), \
                patch.object(display.termios, "tcgetattr", return_value=[]), \
                patch.object(display.termios, "tcsetattr"), patch.object(display.tty, "setcbreak"), \
                patch.object(display.select, "select", return_value=([], [], [])), \
                patch.object(display.subprocess, "Popen", return_value=process) as started, \
                contextlib.redirect_stdout(TerminalOutput()) as output:
            self.assertEqual(display.ssh_display(Path("/installed/tool"), "Test tablet"), 0)
        command = started.call_args.args[0]
        self.assertIn("--property=RuntimeMaxSec=310", command)
        self.assertIn("--property=LimitCORE=0", command)
        self.assertIn("--property=SuccessExitStatus=130", command)
        self.assertIn("--remote-worker", command)
        self.assertNotIn("qrText", output.getvalue())

    def test_disconnect_stops_only_this_invocations_unit(self):
        process = MagicMock()
        process.poll.side_effect = [None, None, 0]
        with patch.object(display, "require_enabled"), \
                patch.object(display, "monitor_connected", return_value=True), \
                patch.object(display.sys.stdin, "isatty", return_value=True), \
                patch.object(display.sys.stdin, "fileno", return_value=0), \
                patch.object(display.termios, "tcgetattr", return_value=[]), \
                patch.object(display.termios, "tcsetattr"), patch.object(display.tty, "setcbreak"), \
                patch.object(display.select, "select", return_value=([sys.stdin], [], [])), \
                patch.object(display.os, "read", return_value=b""), \
                patch.object(display.subprocess, "Popen", return_value=process) as started, \
                patch.object(display.subprocess, "run") as stopped, \
                contextlib.redirect_stdout(TerminalOutput()):
            self.assertEqual(display.ssh_display(Path("/installed/tool"), "Test"), 130)
        unit = next(arg.split("=", 1)[1] for arg in started.call_args.args[0] if arg.startswith("--unit="))
        stopped.assert_called_once_with(["/usr/bin/systemctl", "stop", unit], check=True, timeout=20)

    def test_normal_ssh_still_rejected_without_explicit_mode(self):
        with patch.object(tool.os, "geteuid", return_value=0), \
                patch.object(tool, "require_local_console", side_effect=ValueError("not SSH")), \
                patch.object(tool.socket, "socket") as socket, \
                patch.object(sys, "argv", ["gatekeeper-enroll", "--name", "Test"]), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(), 2)
        socket.assert_not_called()

    def test_status_ssh_requires_policy_and_never_issues_qr(self):
        with patch.object(tool.os, "geteuid", return_value=0), \
                patch.object(tool, "require_enabled") as enabled, \
                patch.object(tool, "enrollment_status", return_value={"enrollment_ready": True}), \
                patch.object(tool.socket, "socket") as socket, \
                patch.object(sys, "argv", ["gatekeeper-enroll", "--ssh-display", "--status"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(), 0)
        enabled.assert_called_once()
        socket.assert_not_called()

    def test_busy_display_never_issues_qr(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tool, "LOCK", str(Path(directory) / "lock")), \
                patch.object(tool.os, "geteuid", return_value=0), \
                patch.object(tool, "require_local_console"), \
                patch.object(tool.fcntl, "flock", side_effect=BlockingIOError), \
                patch.object(tool.socket, "socket") as socket, \
                patch.object(sys, "argv", ["gatekeeper-enroll", "--name", "Test"]), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(), 2)
        socket.assert_not_called()

    def test_signals_enter_cleanup_path(self):
        with self.assertRaises(KeyboardInterrupt):
            display.interrupted(signal.SIGHUP, None)

    def test_worker_clears_display_and_closes_token_on_every_end_path(self):
        for ending in ("redeemed", "keyboard", "expired", "disconnect", "signal"):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as directory:
                screen = MagicMock()
                screen.fd = 8
                surface = MagicMock()
                stream = MagicMock()
                issued = {"ok": True, "expiresAt": 999000 if ending == "expired" else 1300000,
                          "qrText": json.dumps({"schema": "revpi-tablet-enrollment/v1",
                                                "caCertificate": "test-public-ca"})}
                responses = [(issued, b""), ({"ended": True}, b"")]
                if ending == "disconnect":
                    responses[1] = ValueError("Local enrollment service disconnected.")
                if ending == "signal":
                    responses[1] = KeyboardInterrupt()
                readable = [8] if ending == "keyboard" else [stream]
                with patch.object(tool.os, "geteuid", return_value=0), \
                        patch.object(tool, "require_enabled"), \
                        patch.object(tool, "LOCK", str(Path(directory) / "lock")), \
                        patch.object(tool, "PhysicalDisplay", return_value=screen), \
                        patch.object(tool.socket, "socket", return_value=stream), \
                        patch.object(tool, "read_record", side_effect=responses), \
                        patch.object(tool.subprocess, "run",
                                     return_value=subprocess.CompletedProcess([], 0, "test-xpm")), \
                        patch.object(tool, "console_module"), \
                        patch.object(tool, "framebuffer_matrix", return_value=(surface, b"saved", {})), \
                        patch.object(tool.time, "time", return_value=1000), \
                        patch.object(tool.select, "select", return_value=(readable, [], [])), \
                        patch.object(tool.os, "read", return_value=b"x"), \
                        patch.object(sys, "argv", ["gatekeeper-enroll", "--remote-worker", "--name", "Test"]), \
                        contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    expected = 2 if ending == "disconnect" else 130 if ending == "signal" else 0
                    self.assertEqual(tool.main(), expected)
                surface.__setitem__.assert_called_once_with(slice(0, 5), b"saved")
                surface.close.assert_called_once()
                stream.close.assert_called_once()
                screen.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
