import json

from strata_launcher import strata


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_find_in_folder_linux_layouts(tmp_path):
    assert strata.find_in_folder(tmp_path, "linux") is None
    touch(tmp_path / "a" / "bin" / "strata")
    touch(tmp_path / "b" / "strata")
    touch(tmp_path / "c" / "terminal" / "current" / "bin" / "strata")
    for name in "abc":
        assert strata.find_in_folder(tmp_path / name, "linux").endswith("strata")
    assert strata.find_in_folder(tmp_path / "missing", "linux") is None


def test_find_in_folder_windows_layouts(tmp_path):
    touch(tmp_path / "Strata" / "terminal" / "current" / "bin" / "strata.cmd")
    found = strata.find_in_folder(tmp_path / "Strata", "win32")
    assert found and found.endswith("strata.cmd")
    assert strata.find_in_folder(tmp_path / "Strata", "linux") is None          # no file called plain "strata" there


def test_accept_takes_a_folder_or_the_command(tmp_path):
    cmd = touch(tmp_path / "x" / "bin" / "strata")
    assert strata.accept(tmp_path / "x", "linux") == str(cmd)
    assert strata.accept(str(cmd), "linux") == str(cmd)
    assert strata.accept(f'"{cmd}"', "linux") == str(cmd)
    assert strata.accept(touch(tmp_path / "notes.txt"), "linux") is None
    assert strata.accept("", "linux") is None and strata.accept(tmp_path / "nothing", "linux") is None


def test_discover_prefers_the_path_then_the_usual_folders(tmp_path):
    assert strata.discover({"HOME": str(tmp_path)}, "linux", which=lambda name: "/usr/bin/strata") == "/usr/bin/strata"
    assert strata.discover({"HOME": str(tmp_path)}, "linux", which=lambda name: None) is None
    cmd = touch(tmp_path / ".local" / "bin" / "strata")
    assert strata.discover({"HOME": str(tmp_path)}, "linux", which=lambda name: None) == str(cmd)
    win = touch(tmp_path / "Local" / "Strata" / "terminal" / "current" / "bin" / "strata.cmd")
    assert strata.discover({"LOCALAPPDATA": str(tmp_path / "Local")}, "win32", which=lambda name: None) == str(win)
    other = touch(tmp_path / "data" / "terminal" / "current" / "bin" / "strata")
    assert strata.discover({"HOME": str(tmp_path / "empty"), "STRATA_DATA_DIR": str(tmp_path / "data")}, "linux", which=lambda n: None) == str(other)


def test_parse_json_skips_lines_before_it():
    assert strata.parse_json('warning: something\n{"ok": true, "n": 1}\n') == {"ok": True, "n": 1}
    assert strata.parse_json("no json here") is None and strata.parse_json("") is None


def test_parse_version():
    assert strata.parse_version("strata 0.2.11 (build 0.1.57; python 3.12 at /somewhere)") == "0.2.11"
    assert strata.parse_version("command not found") is None


def test_parse_presets_sorted_by_last_use():
    text = json.dumps({"schema_version": 1, "ok": True, "presets": [
        {"name": "Old", "model": "Q2_0", "context": 32768, "access": "local", "port": 8080, "last_used": "2026-01-01 10:00:00"},
        {"name": "New", "model": "Q4", "context": 131072, "access": "lan", "port": 8081, "last_used": "2026-02-01 10:00:00"},
        {"no": "name"}, "junk"]})
    presets = strata.parse_presets(text)
    assert [p["name"] for p in presets] == ["New", "Old"]
    assert presets[0]["about"] == "Q4, 128K, network, port 8081"
    assert strata.parse_presets("garbage") == []


def test_parse_models():
    text = json.dumps({"ok": True, "models": [
        {"id": "q2_0", "name": "family-q2_0", "context_tokens": 65536, "component_status": "present", "missing": []},
        {"id": "q4", "name": "family-q4", "component_status": "incomplete", "missing": ["pack"]}, {"name": "no id"}]})
    models = strata.parse_models(text)
    assert [m["id"] for m in models] == ["q2_0", "q4"]
    assert models[0]["complete"] and models[0]["context"] == "64K" and not models[1]["complete"]


def test_parse_status_json_plain_and_nothing():
    text = json.dumps({"ok": True, "running": [{"name": "office", "state": "ready", "port": 8080, "url": "http://127.0.0.1:8080",
                                                "host": "0.0.0.0", "config": "/state/launch/office.json"}]})
    assert strata.parse_status(text)[0]["config"] == "/state/launch/office.json"
    plain = strata.parse_status("office: ready at http://127.0.0.1:8081\n")
    assert plain == [{"name": "office", "state": "ready", "url": "http://127.0.0.1:8081", "host": "", "config": "", "port": 8081}]
    assert strata.parse_status("Nothing is serving.") == []
    assert strata.parse_status(json.dumps({"ok": True, "running": []})) == []


def test_parse_plan():
    plan = strata.parse_plan(json.dumps({"ok": True, "plan": {
        "warnings": ["It is a tight fit: close other programs."], "flags": "strata launch --model q2_0",
        "model": {"title": "Family Q2_0"}, "download": {"gb": 41.5, "text": "41.5 GB to download.", "engine_missing": True}}}))
    assert plan["ok"] and plan["download_gb"] == 41.5 and plan["engine_missing"] and plan["title"] == "Family Q2_0"
    assert plan["warnings"] == ["It is a tight fit: close other programs."]
    nothing = strata.parse_plan(json.dumps({"ok": True, "plan": {"download": {"gb": 0, "text": "Nothing to download."}}}))
    assert nothing["download_gb"] == 0
    refused = strata.parse_plan(json.dumps({"ok": False, "error": "No model is called q9."}))
    assert not refused["ok"] and "q9" in refused["error"]
    assert strata.parse_plan("usage: strata launch ...") is None


def test_parse_key():
    assert strata.parse_key("API key: sk-strata-abc123_x\n") == "sk-strata-abc123_x"
    assert strata.parse_key("This launch has no API key.") is None


def test_launch_files_from_the_run_file(tmp_path):
    run = tmp_path / "launch" / "office.json"
    run.parent.mkdir()
    run.write_text(json.dumps({"log": str(tmp_path / "logs" / "office.engine.log"), "args": ["--pack", str(tmp_path / "models" / "packs" / "q2")]}), encoding="utf-8")
    other = tmp_path / "launch" / "second.json"
    other.write_text("{}", encoding="utf-8")
    status = [{"name": "second", "port": 8081, "config": str(other)}, {"name": "office", "port": 8080, "config": str(run)}]
    files = strata.launch_files(status, 8080)
    assert files["engine_log"] == str(tmp_path / "logs" / "office.engine.log")
    assert files["model_dir"] == str(tmp_path / "models" / "packs")
    assert strata.launch_files(status, 8081)["engine_log"] == str(tmp_path / "launch" / "second.engine.log")    # beside the run file
    assert strata.launch_files([], 8080) == {"engine_log": None, "model_dir": None}
    assert strata.launch_files([{"name": "x", "port": 1, "config": ""}])["engine_log"] is None


UPDATE = {"schema_version": 1, "ok": True, "feed": {"source": "https://example.invalid/releases/latest/download", "channel": "stable"},
          "engine": {"installed": {"version": "0.1.57", "backend": "cuda"}, "offered": {"version": "0.1.59", "size": 723498702},
                     "status": "available", "download_bytes": 723498702, "problem": None},
          "control": {"installed": "0.1.57", "offered": "0.1.59.1", "status": "available", "download_bytes": 100087576},
          "app": {"installed": None, "offered": "0.2.13", "status": "unknown-installed", "download_bytes": 305326584},
          "serving": [{"name": "office"}]}


def test_parse_update_check_available():
    found = strata.parse_update_check("  Reading the signed update feed\n" + json.dumps(UPDATE))
    assert found["ok"] and found["available"] and found["serving"] == 1 and found["source"].startswith("https://example.invalid")
    assert [(p["name"], p["installed"], p["offered"]) for p in found["parts"]] == [("Engine", "0.1.57", "0.1.59"), ("Control", "0.1.57", "0.1.59.1")]
    assert round(found["download_mb"]) == 824                    # the desktop app's installer is not part of `strata update`
    assert strata.update_text(found) == "A newer Strata is available: Engine 0.1.57 → 0.1.59, Control 0.1.57 → 0.1.59.1 (about 824 MB to download)."
    assert strata.update_key(found) == "Engine=0.1.59;Control=0.1.59.1"


def test_parse_update_check_up_to_date_and_odd_answers():
    same = dict(UPDATE, engine={"installed": {"version": "0.1.59"}, "offered": {"version": "0.1.59"}, "status": "current", "download_bytes": 0},
                control={"installed": "0.1.59.1", "offered": "0.1.59.1", "status": "current"}, serving=[])
    found = strata.parse_update_check(json.dumps(same))
    assert found["ok"] and not found["available"] and found["parts"] == [] and found["serving"] == 0
    assert strata.update_text(found) == "Strata is up to date (engine 0.1.59, control 0.1.59.1)." and strata.update_key(found) == ""
    held = dict(same, engine={"installed": {"version": "0.1.57"}, "offered": {"version": "0.1.59"}, "status": "blocked", "problem": "the driver is too old"})
    found = strata.parse_update_check(json.dumps(held))
    assert not found["available"] and found["problem"] == "the driver is too old"       # offered but not installable: no Update button
    failed = strata.parse_update_check(json.dumps({"ok": False, "error": "the feed could not be read", "engine": {}}))
    assert not failed["ok"] and not failed["available"] and "feed" in failed["problem"]
    assert strata.parse_update_check("command not found") is None
    assert strata.parse_update_check(json.dumps({"ok": True, "presets": []})) is None      # some other command's answer
