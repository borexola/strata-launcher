#!/usr/bin/env python3
"""Writes a settings folder that points the launcher at the stand-in for Strata (tools/fake_strata).

    python tools/make_demo_config.py FOLDER [--port 18431] [--access local|lan]

Then run the launcher with STRATA_LAUNCHER_CONFIG_DIR=FOLDER. Used by the self-test, the screenshots and manual tries.
Nothing here touches a real Strata.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--port", type=int, default=18431)
    ap.add_argument("--access", default="local")
    ap.add_argument("--no-strata", action="store_true", help="leave the Strata folder unset, to try the first run")
    a = ap.parse_args()
    folder = Path(a.folder)
    folder.mkdir(parents=True, exist_ok=True)
    command = HERE / "fake_strata" / "bin" / ("strata.cmd" if os.name == "nt" else "strata")
    cfg = {"version": 2, "strata_command": "" if a.no_strata else str(command),
           "source": {"kind": "model", "family": "", "model": "demo-q4", "model_path": "", "preset": "", "data_dir": ""},
           "preset_name": "Strata Launcher", "values": {"context": "32k", "parallel": "2", "access": a.access, "port": a.port},
           "check_updates": False, "nvidia_smi": str(HERE / "fake_strata" / "nvidia-smi.py")}
    (folder / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    print(folder / "config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
