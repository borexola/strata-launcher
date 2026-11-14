import os
import socket
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tools" / "fake_strata"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """A settings folder and a stand-in Strata of their own for one test. Returns a dict with the paths and the port."""
    port = free_port()
    cfg_dir, fake_home = tmp_path / "settings", tmp_path / "fake"
    monkeypatch.setenv("STRATA_LAUNCHER_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("FAKE_STRATA_HOME", str(fake_home))
    monkeypatch.setenv("FAKE_STRATA_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_STRATA_LOAD_S", "0.2")
    # Tests never read or write the real start-at-sign-in entry of the person running them.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    from strata_launcher import autostart

    class NoRegistry(dict):
        get = dict.get

        def set(self, name, value):
            self[name] = value

        def delete(self, name):
            self.pop(name, None)
    sandbox = (autostart.WindowsAutostart(NoRegistry()) if os.name == "nt"
               else autostart.LinuxAutostart(env={"XDG_CONFIG_HOME": str(tmp_path / "xdg-config"), "XDG_DATA_HOME": str(tmp_path / "xdg-data")}))
    monkeypatch.setattr(autostart, "get", lambda *a, **k: sandbox)
    monkeypatch.setenv("STRATA_LAUNCHER_UPDATE_URL", "http://127.0.0.1:9/latest")      # tests never ask GitHub
    command = FAKE / "bin" / ("strata.cmd" if os.name == "nt" else "strata")
    return {"port": port, "cfg_dir": cfg_dir, "fake_home": fake_home, "strata": str(command), "url": f"http://127.0.0.1:{port}",
            "nvidia_smi": str(FAKE / "nvidia-smi.py")}
