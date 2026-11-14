#!/usr/bin/env python3
"""Makes the pictures for the README: every tab of the real window, fed by the stand-in for Strata.

Every figure, name and path in the pictures is made up: the server is tools/fake_strata, the graphics cards come
from its stand-in nvidia-smi, and the PC's memory and processor figures come from DemoProbe below. Nothing is read
from the computer the pictures are made on.

    python tools/make_screenshots.py OUT_FOLDER [--theme light|dark] [--suffix=-dark] [--tabs settings,performance,log] [--max-height 1200]

The README's pictures, on Linux without a screen:
    QT_QPA_PLATFORM=offscreen python tools/make_screenshots.py docs/screenshots --theme dark --tabs settings,performance,log --max-height 1130
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
PORT = 18441
HEIGHTS = {"settings": 1330, "performance": 2060, "log": 760, "setup": 1230, "app": 1080}


class DemoProbe:
    """Made-up figures for the PC, so the pictures show nothing about the computer they were made on."""

    def system(self, now):
        return {"ram_total": 48.0, "ram_avail": 26.2, "swap_total": 8.0, "swap_free": 7.6, "swap_in_mb_s": 0.0,
                "swap_out_mb_s": 0.0, "cpu_pct": 23.0}

    def engine(self, now):
        return {"engine_pid": 4242, "eng_locked": 6.4, "eng_work": 3.1, "eng_files": 1.2, "eng_swap": 0.0, "disk_mb_s": 0.0,
                "disk_total_gb": 23.4}

    def drives(self, model_dir):
        return [{"id": "demo0", "model": "Demo NVMe 1TB", "temp": 47.0, "hottest": 55.0, "warn": 84.0, "crit": 88.0, "holds_model": True}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--theme", default="light", choices=("light", "dark"))
    ap.add_argument("--suffix", default="")
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--max-height", type=int, default=0, help="never make a picture taller than this (a long tab is then cut off at the bottom)")
    ap.add_argument("--tabs", default="", help="only these tabs, for example settings,performance,log")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="strata-launcher-shots-"))
    home, fake_home, cfg_dir = work / "home", work / "fake", work / "home" / ".config" / "strata-launcher"
    home.mkdir()
    os.environ.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / ".config"), FAKE_STRATA_HOME=str(fake_home),
                      FAKE_STRATA_PYTHON=sys.executable, FAKE_STRATA_SEED="14", FAKE_STRATA_HOLD="answering", FAKE_STRATA_HELPER="1",
                      FAKE_STRATA_LOAD_S="0.3", FAKE_STRATA_STARTED="2026-01-15 10:30:00", STRATA_LAUNCHER_CONFIG_DIR=str(cfg_dir))
    subprocess.run([sys.executable, str(HERE / "make_demo_config.py"), str(cfg_dir), "--port", str(PORT), "--access", "lan"],
                   check=True, stdout=subprocess.DEVNULL)
    fake = [sys.executable, str(HERE / "fake_strata" / "fake_strata.py")]
    subprocess.run(fake + ["launch", "--model", "demo-q4", "--context", "32k", "--parallel", "2", "--access", "lan", "--port", str(PORT),
                           "--save-preset", "Strata Launcher", "--restart", "--yes", "--no-tui"], check=True, stdout=subprocess.DEVNULL)
    try:
        from PySide6.QtCore import QTimer

        from strata_launcher import config
        from strata_launcher.ui import app as ui_app
        from strata_launcher.ui.window import TABS

        cfg, _notes, _existed = config.load()
        cfg["theme"] = a.theme
        config.save(cfg)
        app, win = ui_app.create([sys.argv[0]], single=False, probe=DemoProbe())
        shown = "C:\\Users\\you\\AppData\\Local\\Strata\\terminal\\current\\bin\\strata.cmd" if os.name == "nt" else "/home/you/.local/bin/strata"
        state = {"tab": 0, "t": time.time(), "ready": False}
        wanted = [t for t in a.tabs.split(",") if t]

        def tick():
            now = time.time()
            if not state["ready"]:
                if now - state["t"] < 7 or not win.sample.get("metrics") or not win.info.get("version"):
                    return
                # Display only: a neutral path instead of where the stand-in lives on this computer.
                win.loading = True
                win.cfg["strata_command"] = shown
                win.strata_edit.setText(shown)
                win.loading = False
                win.refresh_preview()
                win.dirty = False
                win.refresh_buttons()
                win.say("Settings saved. They take effect at the next Start.")
                win.say("Strata is ready at " + win.url())
                state["ready"] = True
                state["t"] = now
                return
            if now - state["t"] < 0.8:
                return
            if state.get("grab"):
                name = TABS[state["tab"]]
                path = out / f"{name}{a.suffix}.png"
                win.grab().save(str(path))
                print("wrote", path)
                state["grab"] = False
                state["tab"] += 1
                state["t"] = now
                if state["tab"] >= len(TABS):
                    timer.stop()
                    win.close()
                    app.quit()
                return
            name = TABS[state["tab"]]
            if wanted and name not in wanted:
                state["tab"] += 1
                if state["tab"] >= len(TABS):
                    timer.stop()
                    win.close()
                    app.quit()
                return
            win.tabs.setCurrentIndex(state["tab"])
            win.resize(a.width, min(HEIGHTS[name], a.max_height) if a.max_height else HEIGHTS[name])
            state["grab"] = True
            state["t"] = now

        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(200)
        win.resize(a.width, 900)
        win.show()
        app.exec()
    finally:
        subprocess.run(fake + ["launch", "--stop"], stdout=subprocess.DEVNULL)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
