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
    monkeypatch.delenv("STRATA_LAUNCHER_UPDATE_URL", raising=False)
    command = FAKE / "bin" / ("strata.cmd" if os.name == "nt" else "strata")
    return {"port": port, "cfg_dir": cfg_dir, "fake_home": fake_home, "strata": str(command), "url": f"http://127.0.0.1:{port}",
            "nvidia_smi": str(FAKE / "nvidia-smi.py")}
