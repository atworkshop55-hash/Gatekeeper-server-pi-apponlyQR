import importlib.machinery
import importlib.util
from pathlib import Path
import socket
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "scripts" / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def receive(stream, size):
    result = b""
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise ValueError("Incomplete loopback PLC request.")
        result += chunk
    return result
