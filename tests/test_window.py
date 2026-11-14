"""The real window, drawn without a screen, against the stand-in for Strata."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from strata_launcher import command, config, options  # noqa: E402
from strata_launcher.sampler import http_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def write_config(env, **extra):
    env["cfg_dir"].mkdir(parents=True, exist_ok=True)
    cfg = {"version": 2, "strata_command": env["strata"], "source": {"kind": "model", "model": "demo-q4"},
           "values": {"port": env["port"], "context": "32k", "parallel": "2"}, "check_updates": False, "check_strata_updates": False,
           "nvidia_smi": "off"}
    cfg.update(extra)
    (env["cfg_dir"] / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def pump(check, seconds=20.0):
    """Lets the window work until check() is true."""
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        if check():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window(fake_env):
    from strata_launcher.ui import app as ui_app
    write_config(fake_env)
    app, win = ui_app.create(["test"], single=False)
    win.show()
    yield win
    win.close()
    pump(lambda: False, 0.2)
    subprocess.run([sys.executable, str(ROOT / "tools" / "fake_strata" / "fake_strata.py"), "launch", "--stop"], capture_output=True)


def test_window_builds_every_tab_and_setting(window):
    opts, _ = options.load()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Settings", "Performance", "Log", "Setup", "App"]
    assert set(window.fields) == {s["key"] for s in opts["settings"]}
    assert window.get_field("port") == window.cfg["values"]["port"] and window.get_field("context") == "32k"
    assert window.status_title.text() == "Stopped"
    assert not window.btn_save.isEnabled() and window.btn_start.isEnabled() and not window.btn_stop.isEnabled()
    assert window.preview.text() == command.printable(command.launch_command(window.cfg, window.opts))
    assert not window.sw_login.isChecked()                    # start at sign-in is off unless switched on


def test_strata_lists_fill_the_setup_tab(window):
    assert pump(lambda: window.info.get("version") == "0.0.0")
    assert [window.model_combo.itemText(i) for i in range(window.model_combo.count())] == ["demo-q4", "demo-q2"]
    assert window.preset_combo.count() >= 1 and window.model_combo.currentText() == "demo-q4"
    assert "2 models set up" in window.strata_note.text()


def test_changing_a_setting_then_save_writes_the_file(window, fake_env):
    window.set_field("context", "64k")
    window.set_field("reserve", 1024)
    window.fields["gpu"][1].setText("0,1")
    window.extra_edit.setText("--set example=on")
    assert window.dirty and window.btn_save.isEnabled()
    assert "--context 64k" in window.preview.text() and "--set vram-reserve-mib=1024" in window.preview.text()
    assert window.preview.text().index("--set example=on") < window.preview.text().index("--save-preset")
    window.btn_save.click()
    saved, _notes, existed = config.load(fake_env["cfg_dir"] / "config.json")
    assert existed and not window.dirty
    assert saved["values"]["context"] == "64k" and saved["values"]["reserve"] == 1024 and saved["values"]["gpu"] == "0,1"
    assert saved["extra_flags"] == "--set example=on"
    window.btn_defaults.click()
    assert window.get_field("context") == "" and window.get_field("reserve") is None and window.dirty


def test_a_typed_value_and_the_three_kinds_of_source(window):
    window.fields["context"][1].setEditText("48000")
    assert window.get_field("context") == "48000"
    window.kind_buttons["preset"].click()
    window.preset_combo.setEditText("Demo 32K")
    cfg = window.collect()
    assert cfg["source"]["kind"] == "preset" and command.source_flags(cfg["source"]) == ["--preset", "Demo 32K"]
    assert not window.data_row.isVisible()
    window.kind_buttons["files"].click()
    window.files_edit.setText("/models/x.gguf")
    assert "--model-path /models/x.gguf" in window.preview.text()


def test_start_twice_runs_one_start_then_stop(window, fake_env):
    window.btn_start.click()
    window.btn_start.click()
    window.btn_start.click()
    assert window.busy == "Starting…" and not window.btn_start.isEnabled()
    assert sum(1 for t in threading.enumerate() if t.name == "action") == 1
    assert pump(lambda: window.busy is None and bool(window.sample.get("health")), 30)
    assert window.status_title.text() in ("Running", "Answering…", "Reading the prompt…", "Thinking…")
    assert window.btn_stop.isEnabled() and window.btn_start.text() == "Restart with these settings"
    assert window.runner.most_active <= 3                     # a start, a question to Strata and the lists being read may overlap
    assert pump(lambda: any(k == "start" for k, _t in window.records), 15)          # the engine log reached the Log tab
    assert fake_env["url"] in window.status_sub.text()
    window.btn_stop.click()
    window.btn_stop.click()
    assert pump(lambda: window.busy is None and not window.sample.get("health"), 30)
    assert window.status_title.text() == "Stopped" and http_json(fake_env["url"] + "/health", timeout=0.5)[0] == 0
    assert any("Strata is stopped" in t for _k, t in window.records)
    pump(lambda: False, 3)                                   # a wanted stop is never reported as a crash
    assert not any("stopped by itself" in t for _k, t in window.records)


def test_a_server_that_disappears_is_reported(window, fake_env):
    window.btn_start.click()
    assert pump(lambda: window.busy is None and bool(window.sample.get("health")), 30)
    assert pump(lambda: window.was_ready, 5)
    subprocess.run([sys.executable, str(ROOT / "tools" / "fake_strata" / "fake_strata.py"), "launch", "--stop"], capture_output=True)   # not through the window
    assert pump(lambda: any(k == "error" and "stopped by itself" in t for k, t in window.records), 15)
    assert window.status_title.text() == "Stopped"


def test_first_setup_picks_a_model_that_is_there(fake_env):
    from strata_launcher.ui import app as ui_app
    write_config(fake_env)
    path = fake_env["cfg_dir"] / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["source"]["model"] = ""
    path.write_text(json.dumps(cfg), encoding="utf-8")
    app, win = ui_app.create(["test"], single=False)
    try:
        assert pump(lambda: win.info.get("version") == "0.0.0")
        assert win.model_combo.currentText() == "demo-q4" and win.dirty
        assert command.source_problem(win.collect()["source"]) is None
    finally:
        win.close()


def test_a_failed_start_says_so_and_frees_the_buttons(window, monkeypatch):
    monkeypatch.setenv("FAKE_STRATA_FAIL", "1")
    window.btn_start.click()
    assert pump(lambda: window.busy is None, 30)
    assert any(k == "error" and "did not start" in t for k, t in window.records)
    assert window.btn_start.isEnabled() and window.status_title.text() == "Stopped"


def test_nothing_starts_without_a_model_or_without_strata(window):
    window.model_combo.setEditText("")
    window.btn_start.click()
    assert window.busy is None and window.tabs.currentIndex() == 3
    assert any("Choose a model" in t for _k, t in window.records)


def test_a_download_is_never_started_without_a_yes(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    window.model_combo.setEditText("not-installed")
    window.btn_start.click()
    assert pump(lambda: bool(asked) and window.busy is None, 30)
    assert "12.3 GB" in asked[0]
    assert pump(lambda: any("Nothing was downloaded" in t for _k, t in window.records), 5)
    assert not window.sample.get("health")


def test_start_at_sign_in_switch_writes_and_removes_the_entry(window, tmp_path, monkeypatch):
    from strata_launcher import autostart
    window.autostart = autostart.LinuxAutostart(env={"XDG_CONFIG_HOME": str(tmp_path)})
    window.sw_login.setChecked(True)
    assert window.autostart.path.exists() and "--at-login" in window.autostart.path.read_text(encoding="utf-8")
    assert window.row_minimized.isEnabled()
    window.sw_login.setChecked(False)
    assert not window.autostart.path.exists() and not window.row_minimized.isEnabled()


def test_hardware_rows_keep_their_order_whatever_arrives_first(window):
    from strata_launcher.ui.widgets import Meter
    card_b = {"index": 1, "name": "Card B", "used": 100.0, "total": 1000.0, "util": 5.0}
    card_a = {"index": 0, "name": "Card A", "used": 100.0, "total": 1000.0, "util": 5.0}
    base = {"cpu_pct": 10.0, "ram_total": 32.0, "ram_avail": 20.0, "swap_total": 4.0, "swap_free": 4.0}
    window.refresh_hardware(base, [card_b])                   # the second card and the PC's own figures arrive first
    window.refresh_hardware(base, [card_a, card_b])
    box = window.hw_card.box
    titles = [box.itemAt(i).widget().title.text() for i in range(box.count()) if isinstance(box.itemAt(i).widget(), Meter)]
    assert titles == ["Card A memory", "Card A load", "Card B memory", "Card B load", "Processor load", "RAM in use", "Swap in use"]


def test_an_update_shows_on_the_app_tab(window):
    window.on_update({"ok": True, "newer": True, "latest": "9.9.9", "page": "https://example.invalid/r", "notes": "", "asset": None, "by_hand": True})
    assert "9.9.9" in window.update_label.text() and not window.btn_get.isHidden()
    assert window.tabs.tabText(4) != "App"
    window.on_update({"ok": True, "newer": False, "latest": "0.0.1", "page": "", "notes": "", "asset": None})
    assert window.btn_get.isHidden() and "newest" in window.update_label.text()
    window.on_update({"ok": False, "why": "No answer."})
    assert "No answer." in window.update_label.text()


def test_first_run_without_strata_opens_on_setup(fake_env, monkeypatch):
    from strata_launcher import strata
    from strata_launcher.ui import app as ui_app
    monkeypatch.setattr(strata, "discover", lambda *a, **k: None)
    app, win = ui_app.create(["test"], single=False)
    try:
        assert win.cfg["strata_command"] == "" and win.tabs.currentIndex() == 3
        assert win.status_title.text() == "Not set up yet"
        assert (fake_env["cfg_dir"] / "config.json").exists()           # the first run writes the settings file
        win.use_strata(strata.accept(Path(fake_env["strata"]).parent.parent), "x")
        assert win.cfg["strata_command"] == fake_env["strata"]
        assert pump(lambda: win.info.get("version") == "0.0.0")
    finally:
        win.close()


def test_a_broken_settings_list_is_reported_not_fatal(fake_env):
    from strata_launcher.ui import app as ui_app
    write_config(fake_env)
    (fake_env["cfg_dir"] / "options.json").write_text(json.dumps({"settings": [{"key": "bad", "type": "slider", "label": "x"}], "remove": ["speed"]}), encoding="utf-8")
    app, win = ui_app.create(["test"], single=False)
    try:
        assert "speed" not in win.fields and "bad" not in win.fields
        assert any(k == "warn" and "options.json" in t for k, t in win.records)
    finally:
        win.close()


@pytest.mark.skipif(os.environ.get("SKIP_SELFTEST") == "1", reason="asked to skip")
def test_the_self_test_passes(fake_env):
    write_config(fake_env, nvidia_smi=fake_env["nvidia_smi"])
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-m", "strata_launcher", "--selftest", "--selftest-seconds", "3"], env=env, capture_output=True, text=True, timeout=120)
    assert "selftest: PASS" in p.stdout, p.stdout + p.stderr
    assert p.returncode == 0
    report = (fake_env["cfg_dir"] / "logs" / "selftest.txt").read_text(encoding="utf-8")
    assert "before |" in report and "after the window closed" in report


def test_the_self_test_refuses_anything_but_the_stand_in(fake_env, tmp_path):
    other = tmp_path / ("strata.cmd" if os.name == "nt" else "strata")
    other.write_text("@echo strata 1.2.3\n" if os.name == "nt" else "#!/bin/sh\necho 'strata 1.2.3'\n", encoding="utf-8")
    other.chmod(0o755)
    write_config(fake_env, strata_command=str(other))
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-m", "strata_launcher", "--selftest"], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "REFUSED" in p.stdout


def test_old_downloads_are_tidied_but_fresh_ones_and_other_files_stay(tmp_path):
    from strata_launcher.ui.app import tidy_temp
    now = 2_000_000_000.0
    old_update, new_update = tmp_path / "strata-launcher-update-old", tmp_path / "strata-launcher-update-new"
    old_out, new_out, other = tmp_path / "strata-launcher-abc.out", tmp_path / "strata-launcher-def.out", tmp_path / "someone-elses-file.out"
    for d in (old_update, new_update):
        d.mkdir()
        (d / "x.msi").write_bytes(b"x")
    for f in (old_out, new_out, other):
        f.write_text("x", encoding="utf-8")
    for path, age in ((old_update, 7200), (new_update, 60), (old_out, 2 * 86400), (new_out, 3600), (other, 9 * 86400)):
        os.utime(path, (now - age, now - age))
    assert tidy_temp(now, str(tmp_path)) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["someone-elses-file.out", "strata-launcher-def.out", "strata-launcher-update-new"]


def versions(env):
    path = env["fake_home"] / "versions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"engine": "0.0.0", "control": "0.0.0"}


def answer(monkeypatch, reply):
    """Answers the launcher's yes/no questions without a person. Returns the list of questions asked."""
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or reply)
    return asked


def test_strata_update_check_says_up_to_date(window):
    assert window.btn_strata_update.isHidden()
    window.btn_strata_check.click()
    assert pump(lambda: window.strata_found is not None)
    assert "up to date" in window.strata_update_label.text() and window.btn_strata_update.isHidden()
    assert window.tabs.tabText(3) == "Setup"


def test_strata_update_is_offered_and_never_runs_without_a_yes(window, fake_env, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv("FAKE_STRATA_OFFER", "0.0.2")
    window.btn_strata_check.click()
    assert pump(lambda: window.strata_found is not None)
    assert "Engine 0.0.0 → 0.0.2" in window.strata_update_label.text() and not window.btn_strata_update.isHidden()
    assert window.tabs.tabText(3) != "Setup" and any("newer Strata is available" in t for _k, t in window.records)
    asked = answer(monkeypatch, QMessageBox.No)
    spawned = window.runner.spawned
    window.btn_strata_update.click()
    assert len(asked) == 1 and "signed update feed" in asked[0] and "A model is loaded" not in asked[0]
    assert window.busy is None and window.runner.spawned == spawned and versions(fake_env)["engine"] == "0.0.0"
    assert any("was not updated" in t for _k, t in window.records)


def test_strata_update_with_no_model_loaded(window, fake_env, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv("FAKE_STRATA_OFFER", "0.0.2")
    window.btn_strata_check.click()
    assert pump(lambda: window.strata_found is not None)
    answer(monkeypatch, QMessageBox.Yes)
    window.btn_strata_update.click()
    assert window.busy == "Updating Strata…" and not window.btn_start.isEnabled()
    assert pump(lambda: window.busy is None, 30)
    assert versions(fake_env) == {"engine": "0.0.2", "control": "0.0.2"}
    assert any(k == "launcher" and "Strata is updated." in t for k, t in window.records)
    assert pump(lambda: "up to date" in window.strata_update_label.text(), 10) and window.btn_strata_update.isHidden()
    assert window.tabs.tabText(3) == "Setup"
    log = (fake_env["cfg_dir"] / "logs" / "launcher.log").read_text(encoding="utf-8")
    assert "update --yes" in log and "--stop-model" not in log


def test_strata_update_stops_a_loaded_model_only_after_a_yes_and_starts_it_again(window, fake_env, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv("FAKE_STRATA_OFFER", "0.0.2")
    window.btn_start.click()
    assert pump(lambda: window.busy is None and bool(window.sample.get("health")), 30)
    window.btn_strata_check.click()
    assert pump(lambda: window.strata_found is not None and window.strata_found["serving"] == 1)
    asked = answer(monkeypatch, QMessageBox.Yes)
    window.btn_strata_update.click()
    assert "A model is loaded" in asked[0] and "starts it again" in asked[0]
    assert pump(lambda: window.busy is None, 40)
    assert versions(fake_env)["engine"] == "0.0.2"
    assert pump(lambda: bool(window.sample.get("health")), 15)                 # the model answers again
    log = (fake_env["cfg_dir"] / "logs" / "launcher.log").read_text(encoding="utf-8")
    assert "--stop-model --start-again" in log
    pump(lambda: False, 3)
    assert not any("stopped by itself" in t for _k, t in window.records)     # a wanted stop for an update is not a crash


def test_a_failed_strata_update_says_so_and_keeps_the_offer(window, fake_env, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv("FAKE_STRATA_OFFER", "0.0.2")
    monkeypatch.setenv("FAKE_STRATA_UPDATE_FAIL", "1")
    window.btn_strata_check.click()
    assert pump(lambda: window.strata_found is not None)
    answer(monkeypatch, QMessageBox.Yes)
    window.btn_strata_update.click()
    assert pump(lambda: window.busy is None, 30)
    assert versions(fake_env)["engine"] == "0.0.0"
    assert any(k == "error" and "did not update" in t for k, t in window.records)
    assert pump(lambda: not window.btn_strata_update.isHidden() and window.btn_strata_update.isEnabled(), 10)


def test_strata_updates_are_looked_for_once_a_day_when_switched_on(fake_env, monkeypatch):
    from strata_launcher.ui import app as ui_app
    monkeypatch.setenv("FAKE_STRATA_OFFER", "0.0.3")
    write_config(fake_env, check_strata_updates=True)
    app, win = ui_app.create(["test"], single=False)
    try:
        assert pump(lambda: win.strata_found is not None, 20)                  # nobody pressed anything
        assert win.strata_found["available"] and not win.btn_strata_update.isHidden()
        saved, _notes, _existed = config.load(fake_env["cfg_dir"] / "config.json")
        assert saved["last_strata_check"] > 0 and saved["strata_update_seen"] == "Engine=0.0.3;Control=0.0.3"
        spawned = win.runner.spawned
        win.load_info()                                                        # a second look the same day asks nothing
        assert pump(lambda: win.info.get("version") == "0.0.0")
        pump(lambda: False, 1)
        assert win.runner.spawned == spawned + 3
    finally:
        win.close()
