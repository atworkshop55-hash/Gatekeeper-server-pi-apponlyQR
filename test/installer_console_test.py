import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess


loader = importlib.machinery.SourceFileLoader(
    "console_test", str(Path(__file__).parent.parent / "scripts" / "gatekeeper-console"))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

XPM = '/* XPM */\nstatic char *q[] = {\n"2 2 2 1",\n"  c #FFFFFF",\n"# c #000000",\n"# ",\n" #"\n};'


class ConsoleTests(unittest.TestCase):
    def test_matrix_and_four_module_quiet_zone(self):
        matrix = m.parse_xpm(XPM)
        self.assertEqual(len(matrix), 10)
        self.assertTrue(matrix[4][4])
        self.assertFalse(matrix[4][5])
        self.assertTrue(matrix[5][5])
        for row in matrix[:4] + matrix[-4:]:
            self.assertFalse(any(row))
        for row in matrix:
            self.assertFalse(any(row[:4] + row[-4:]))

    def test_bad_encoder_output_refused(self):
        for value in ("", XPM.replace("2 2 2 1", "2 3 2 1"),
                      XPM.replace("#000000", "#123456"), XPM.replace('" #"', '" xx"')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.parse_xpm(value)

    def test_no_encoder_explicit_error(self):
        with patch.object(m.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "missing"):
                m.display_test_matrix()

    def test_payload_is_only_display_test_and_stdin_not_command_argument(self):
        with patch.object(m.shutil, "which", return_value="/usr/bin/qrencode"), patch.object(
                m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, XPM)) as run:
            m.display_test_matrix()
        self.assertEqual(run.call_args.kwargs["input"], "Gate Keeper DISPLAY TEST ONLY")
        self.assertNotIn(m.TEST_PAYLOAD, run.call_args.args[0])
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-s") + 1], "1")

    def test_root_refused(self):
        with patch.object(m.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "not root"):
                m.require_local_console()

    def test_ssh_terminal_refused(self):
        with patch.object(m.os, "geteuid", return_value=1000), patch.object(
                m.sys.stdin, "isatty", return_value=True), patch.object(
                m.sys.stdout, "isatty", return_value=True), patch.object(
                m.sys.stdin, "fileno", return_value=0), patch.object(
                m.os, "ttyname", return_value="/dev/pts/0"):
            with self.assertRaisesRegex(ValueError, "not SSH"):
                m.require_local_console()

    def test_encoder_failure_not_success_shaped(self):
        with patch.object(m.shutil, "which", return_value="/usr/bin/qrencode"), patch.object(
                m.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "")):
            with self.assertRaisesRegex(ValueError, "failed"):
                m.display_test_matrix()


if __name__ == "__main__":
    unittest.main()
