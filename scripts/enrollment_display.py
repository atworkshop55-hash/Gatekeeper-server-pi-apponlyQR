"""Root-authorized SSH commissioning on a temporary Linux virtual console."""

import fcntl
import os
from pathlib import Path
import select
import secrets
import struct
import subprocess
import sys
import termios
import tty


UNIT_PREFIX = "gatekeeper-enrollment-display-"
POLICY = Path("/etc/gatekeeper/ssh-enrollment-enabled")
LOCK = "/run/gatekeeper-enrollment.lock"


def require_enabled():
    info = POLICY.lstat()
    if POLICY.is_symlink() or not POLICY.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Unsafe SSH enrollment policy file.")
    if POLICY.read_text().strip() != "enabled":
        raise ValueError("SSH-assisted enrollment is not enabled at this site.")


def monitor_connected():
    return any((path / "status").read_text().strip() == "connected"
               for path in Path("/sys/class/drm").glob("card*-HDMI-A-*"))


class PhysicalDisplay:
    def __init__(self):
        self.control = None
        self.fd = None
        self.number = None
        self.previous = None
        self.terminal = None
        self.graphics = False

    def __enter__(self):
        try:
            if not monitor_connected():
                raise ValueError("No connected HDMI monitor; no enrollment QR was issued.")
            self.control = os.open("/dev/tty0", os.O_RDWR | os.O_NOCTTY)
            self.previous = self.active()
            self.number = struct.unpack("i", fcntl.ioctl(self.control, 0x5600, bytes(4)))[0]
            if not 1 <= self.number <= 63 or self.number == self.previous:
                raise ValueError("No unused Linux virtual console available.")
            self.fd = os.open(f"/dev/tty{self.number}", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            mode = struct.unpack("i", fcntl.ioctl(self.fd, 0x4B3B, bytes(4)))[0]
            if mode != 0:
                raise ValueError("Allocated console is not in text mode.")
            self.terminal = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            fcntl.ioctl(self.fd, 0x4B3A, 1)  # KD_GRAPHICS prevents console text overwriting the QR.
            self.graphics = True
            fcntl.ioctl(self.control, 0x5606, self.number)
            fcntl.ioctl(self.control, 0x5607, self.number)
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def active(self):
        return struct.unpack_from("H", fcntl.ioctl(self.control, 0x5603, bytes(6)))[0]

    def check(self):
        if not monitor_connected() or self.active() != self.number:
            raise ValueError("Physical enrollment display disconnected or console changed; cancelling.")

    def __exit__(self, *_):
        try:
            if self.fd is not None:
                try:
                    if self.terminal is not None:
                        termios.tcsetattr(self.fd, termios.TCSANOW, self.terminal)
                finally:
                    if self.graphics:
                        fcntl.ioctl(self.fd, 0x4B3A, 0)
        finally:
            try:
                if self.control is not None and self.number is not None:
                    if self.previous is not None and self.active() == self.number:
                        fcntl.ioctl(self.control, 0x5606, self.previous)
                        fcntl.ioctl(self.control, 0x5607, self.previous)
                    if self.fd is not None:
                        os.close(self.fd)
                        self.fd = None
            finally:
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None
                if self.control is not None:
                    os.close(self.control)
                    self.control = None


def interrupted(signum, _frame):
    raise KeyboardInterrupt


def ssh_display(script, name, display_test=False):
    require_enabled()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Interactive SSH terminal required. Connect using ssh -t.")
    if not monitor_connected():
        raise ValueError("No connected HDMI monitor; no enrollment QR was issued.")
    unit = UNIT_PREFIX + secrets.token_hex(8) + ".service"
    command = [
        "/usr/bin/systemd-run", "--quiet", "--wait", "--collect",
        "--unit=" + unit, "--property=RuntimeMaxSec=310",
        "--property=TimeoutStopSec=10", "--property=UMask=0077",
        "--property=SuccessExitStatus=130",
        "--property=LimitCORE=0", "--property=StandardOutput=journal",
        "--property=StandardError=journal", "--",
        "/usr/bin/python3", "-B", str(script), "--remote-worker", "--name", name,
    ]
    if display_test:
        command.append("--display-test")
    previous = termios.tcgetattr(sys.stdin)
    process = None
    print("Requesting QR on the Pi monitor only. No QR credentials are sent to SSH.")
    print("Any key here or at the Pi cancels. Keep this SSH session open while scanning.", flush=True)
    try:
        tty.setcbreak(sys.stdin)
        process = subprocess.Popen(command)
        while process.poll() is None:
            readable, _, _ = select.select([sys.stdin], [], [], 0.25)
            if readable:
                os.read(sys.stdin.fileno(), 1)
                if process.poll() is None:
                    subprocess.run(["/usr/bin/systemctl", "stop", unit], check=True, timeout=20)
                process.wait(timeout=20)
                print("Enrollment cancelled.")
                return 130
        if process.returncode:
            raise ValueError("Physical display failed; inspect: sudo journalctl -u " + unit +
                             " --no-pager -n 20")
        print("Physical display closed. Check the tablet for successful enrollment.")
        return 0
    finally:
        try:
            if process is not None and process.poll() is None:
                subprocess.run(["/usr/bin/systemctl", "stop", unit], check=True, timeout=20)
                process.wait(timeout=20)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, previous)
