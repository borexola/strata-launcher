"""The self-test: opens the real window, presses its real buttons, and counts threads and programs before and after.

It exists because an earlier panel froze a PC by starting hundreds of helper programs from a timer. The test fails if
the launcher ever runs more than a few programs at once, if threads or programs are left behind, or if readings
come faster than once a second.

It only runs against the stand-in for Strata (tools/fake_strata), never against a real Strata: it presses Start and
Stop, and that must not load or unload anybody's model.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import psutil

from . import command, paths
from .proc import Runner

MOST_PROGRAMS_AT_ONCE = 3        # for example: the long-running card reader, one strata command, one status question
MOST_STARTED = 16                # every program the whole test may start, counted in proc.Runner
MOST_THREADS = 30
MOST_CHILDREN = 10


def run(seconds: float = 6.0) -> int:
    from PySide6.QtCore import QTimer

    from .ui import app as ui_app
    from .ui.window import TABS

    lines: list[str] = []

    def out(text: str):
        text = "selftest: " + text
        lines.append(text)
        print(text, flush=True)

    def finish_report():
        try:
            path = paths.state_dir() / "selftest.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    app, win = ui_app.create([sys.argv[0]], single=False)
    cfg = win.cfg
    if not cfg.get("strata_command"):
        out("FAIL: no strata command is set. Point the settings folder at the stand-in first.")
        finish_report()
        return 2
    _code, version = Runner().capture(command.version_command(cfg["strata_command"]), timeout=30)
    if "stand-in" not in version:
        out("REFUSED: the strata command is not the stand-in for tests. The self-test presses Start and Stop, "
            "so it never runs against a real Strata.")
        finish_report()
        return 2

    me = psutil.Process()
    runner, sampler = win.runner, win.sampler
    problems: list[str] = []
    seen = {"threads": 0, "children": 0, "actions": 0}
    state = {"step": "warm-up", "t": time.time(), "t0": time.time(), "tab": 0, "login_was": None}

    def children() -> list:
        """The programs this launcher started that are still running."""
        found = []
        try:
            for p in me.children(recursive=True):
                try:
                    if p.status() != psutil.STATUS_ZOMBIE:
                        found.append(p)
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
        return found

    def counts() -> str:
        return (f"python threads {threading.active_count()}, all threads {me.num_threads()}, helper programs running {len(children())}, "
                f"programs started so far {runner.spawned}, most at once {runner.most_active}, readings taken {sampler.samples}")

    def step(name: str):
        state.update(step=name, t=time.time())

    def tick():
        now = time.time()
        seen["threads"] = max(seen["threads"], threading.active_count())
        seen["children"] = max(seen["children"], len(children()))
        seen["actions"] = max(seen["actions"], sum(1 for t in threading.enumerate() if t.name == "action"))
        if threading.active_count() > MOST_THREADS or len(children()) > MOST_CHILDREN:
            out(f"RUNAWAY: threads {threading.active_count()}, helper programs {len(children())}. Ending at once.")
            finish_report()
            os._exit(3)
        age, up = now - state["t"], bool(win.sample.get("health"))
        s = state["step"]
        if s == "warm-up" and age > 2.5:
            out(f"before | {counts()}")
            state["before"] = (threading.active_count(), len(children()))
            win.extra_edit.setText(win.extra_edit.text() + " ")          # a real edit, so Save has something to save
            win.btn_save.click()
            out(f"pressed Save | saved file exists: {paths.config_file().exists()}")
            for _ in range(3):                                           # three quick presses: two must be refused
                win.btn_start.click()
            out("pressed Start three times in a row")
            step("starting")
        elif s == "starting" and win.busy is None and up and age > 1:
            out(f"ready after {age:.1f} s | {counts()}")
            step("tabs")
        elif s == "starting" and win.busy is None and not up and age > 20:
            problems.append("the server never answered after Start")
            step("settle")
        elif s == "tabs" and age > 0.7:
            if state["tab"] < len(TABS):
                win.tabs.setCurrentIndex(state["tab"])
                state["tab"] += 1
                state["t"] = now
            else:
                if win.btn_key.isVisible():
                    win.btn_key.click()
                win.btn_plan.click()
                out("opened every tab, pressed Copy API key (if shown) and Check what a start would do")
                step("plan")
        elif s == "plan" and win.busy is None and age > 0.5:
            step("hold")
        elif s == "hold" and age > seconds:
            out(f"after {seconds:.0f} s running | {counts()}")
            for _ in range(2):
                win.btn_stop.click()
            out("pressed Stop twice in a row")
            step("stopping")
        elif s == "stopping" and win.busy is None and not up and age > 1:
            out(f"stopped after {age:.1f} s | {counts()}")
            state["login_was"] = win.autostart.is_enabled()
            win.sw_login.setChecked(not state["login_was"])
            on_now = win.autostart.is_enabled()
            win.sw_login.setChecked(state["login_was"])
            back = win.autostart.is_enabled() == state["login_was"]
            out(f"flipped the start-at-sign-in switch and back | changed: {on_now != state['login_was']}, restored: {back}")
            if on_now == state["login_was"] or not back:
                problems.append("the start-at-sign-in switch did not change the system and change it back")
            step("settle")
        elif s == "stopping" and age > 40:
            problems.append("Stop did not finish in 40 s")
            step("settle")
        elif s == "settle" and age > 4:
            out(f"4 s after Stop | {counts()} | most python threads {seen['threads']}, most helper programs {seen['children']}, "
                f"most actions at once {seen['actions']}, log lines {len(win.records)}")
            after = (threading.active_count(), len(children()))
            before = state["before"]
            elapsed = now - state["t0"]
            if runner.most_active > MOST_PROGRAMS_AT_ONCE:
                problems.append(f"{runner.most_active} programs ran at the same time (limit {MOST_PROGRAMS_AT_ONCE})")
            if runner.spawned > MOST_STARTED:
                problems.append(f"{runner.spawned} programs were started in all (limit {MOST_STARTED})")
            if seen["actions"] > 1:
                problems.append("two start/stop actions ran at the same time")
            if after[0] > before[0] + 1:
                problems.append(f"python threads grew from {before[0]} to {after[0]}")
            if after[1] > before[1]:
                problems.append(f"helper programs grew from {before[1]} to {after[1]}")
            if sampler.samples > elapsed * 1.3 + 2:
                problems.append(f"{sampler.samples} readings in {elapsed:.0f} s: more than one a second")
            if sampler.samples < elapsed * 0.5:
                problems.append(f"only {sampler.samples} readings in {elapsed:.0f} s")
            if not any(k == "answer" or k == "start" for k, _t in win.records):
                problems.append("the engine log was never read")
            timer.stop()
            win.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(250)
    win.show()
    app.exec()
    time.sleep(1.5)
    left = children()
    out(f"after the window closed | helper programs still running: {len(left)}")
    if left:
        problems.append(f"{len(left)} helper programs were left running")
    for p in problems:
        out("PROBLEM: " + p)
    out("PASS" if not problems else "FAIL")
    finish_report()
    return 0 if not problems else 1
