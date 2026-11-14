import json

from strata_launcher import config


def test_missing_file_is_a_first_run(tmp_path):
    cfg, notes, existed = config.load(tmp_path / "config.json")
    assert not existed and notes == []
    assert cfg == config.fresh()
    assert cfg["strata_command"] == "" and cfg["source"]["model"] == ""      # nothing about any PC in the defaults


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "deep" / "config.json"
    cfg = config.fresh()
    cfg["strata_command"] = "/opt/strata/bin/strata"
    cfg["values"] = {"port": 9000, "access": "lan"}
    cfg["source"].update(kind="preset", preset="Office")
    config.save(cfg, path)
    again, notes, existed = config.load(path)
    assert existed and notes == []
    assert again == cfg
    assert not (tmp_path / "deep" / "config.json.tmp").exists()


def test_damaged_file_is_kept_aside(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    cfg, notes, existed = config.load(path)
    assert cfg == config.fresh() and not existed
    assert (tmp_path / "config.json.damaged").exists()
    assert any("could not be read" in n for n in notes)


def test_version_1_is_brought_up(tmp_path):
    old = {"strata": "/opt/strata/bin/strata", "family": "qwen", "model": "q2_0", "data_dir": "/models",
           "base_flags": ["--set", "a=on", "--pool-cores", "2 4"], "preset_name": "Mine", "values": {"port": 8081},
           "engine_log": "/somewhere/x.engine.log", "theme": "dark"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    cfg, notes, existed = config.load(path)
    assert existed and notes
    assert cfg["version"] == config.VERSION
    assert cfg["strata_command"] == "/opt/strata/bin/strata"
    assert cfg["source"] == {"kind": "model", "family": "qwen", "model": "q2_0", "model_path": "", "preset": "", "data_dir": "/models"}
    assert cfg["extra_flags"] == '--set a=on --pool-cores "2 4"'
    assert cfg["preset_name"] == "Mine" and cfg["values"] == {"port": 8081} and cfg["theme"] == "dark"
    assert "strata" not in cfg and "base_flags" not in cfg and "family" not in cfg


def test_version_1_with_a_preset_or_files():
    cfg, _ = config.migrate({"preset": "Office"})
    assert cfg["source"]["kind"] == "preset" and cfg["source"]["preset"] == "Office"
    cfg, _ = config.migrate({"model_path": "/m/x.gguf"})
    assert cfg["source"]["kind"] == "files"


def test_wrong_types_go_back_to_defaults():
    cfg, _ = config.migrate({"version": 2, "check_updates": "yes", "theme": "pink", "window": {"width": "wide", "height": 700},
                             "source": {"kind": "magic", "model": 5}, "preset_name": "  ", "last_update_check": 12.7, "unknown": 1})
    assert cfg["check_updates"] is True and cfg["theme"] == "system"
    assert cfg["window"] == {"width": 1000, "height": 700}
    assert cfg["source"]["kind"] == "model" and cfg["source"]["model"] == ""
    assert cfg["preset_name"] == "Strata Launcher" and cfg["last_update_check"] == 12
    assert "unknown" not in cfg


def test_not_an_object_and_newer_versions():
    cfg, notes = config.migrate([1, 2, 3])
    assert cfg == config.fresh() and notes
    cfg, notes = config.migrate({"version": 99, "theme": "dark", "from_the_future": True})
    assert cfg["theme"] == "dark" and "from_the_future" not in cfg and any("newer version" in n for n in notes)
