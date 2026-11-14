import json

from strata_launcher import options


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_built_in_list_is_sound(tmp_path):
    opts, problems = options.load(user_file=tmp_path / "none.json")
    assert problems == []
    keys = [s["key"] for s in opts["settings"]]
    assert len(keys) == len(set(keys)) and "port" in keys and "access" in keys
    for s in opts["settings"]:
        assert options.check_setting(s) is None
        assert options.valid(s, s["default"])


def test_built_in_defaults_name_no_model_or_machine(tmp_path):
    opts, _ = options.load(user_file=tmp_path / "none.json")
    values = options.defaults(opts)
    assert values["access"] == "local"                      # the safe choice
    assert values["context"] == "" and values["gpu"] == "" and values["reserve"] is None


def test_user_file_replaces_adds_and_removes(tmp_path):
    user = write(tmp_path / "options.json", {
        "settings": [
            {"key": "port", "label": "Port", "type": "number", "default": 9000, "min": 2000, "max": 9999, "flags": ["--port", "{value}"]},
            {"key": "low_ram", "group": "Mine", "label": "Low-RAM mode", "type": "switch", "default": True,
             "on": ["--set", "resident-experts=on"], "off": []},
        ],
        "remove": ["speed", "tune"],
        "address": "http://127.0.0.1:{port}/",
    })
    opts, problems = options.load(user_file=user)
    assert problems == []
    by_key = {s["key"]: s for s in opts["settings"]}
    assert by_key["port"]["default"] == 9000
    assert by_key["low_ram"]["on"] == ["--set", "resident-experts=on"]
    assert "speed" not in by_key and "tune" not in by_key
    assert options.address(opts, options.defaults(opts)) == "http://127.0.0.1:9000"


def test_broken_entries_are_left_out_and_named(tmp_path):
    user = write(tmp_path / "options.json", {"settings": [
        {"key": "a", "label": "A", "type": "slider"},
        {"label": "no key", "type": "text"},
        {"key": "c", "label": "C", "type": "choice", "options": []},
        {"key": "d", "label": "D", "type": "number", "min": 5, "max": 1, "default": 3},
        {"key": "ok", "label": "Fine", "type": "text", "flags": ["--x", "{value}"]},
    ], "remove": "speed"})
    opts, problems = options.load(user_file=user)
    assert [s["key"] for s in opts["settings"]][-1] == "ok"
    assert len(problems) == 5
    assert all("options.json" in p for p in problems)


def test_unreadable_user_file_falls_back(tmp_path):
    bad = tmp_path / "options.json"
    bad.write_text("{ not json", encoding="utf-8")
    opts, problems = options.load(user_file=bad)
    assert len(opts["settings"]) > 5 and len(problems) == 1


def test_clean_values_keeps_what_fits(tmp_path):
    opts, _ = options.load(user_file=tmp_path / "none.json")
    values = options.clean_values(opts, {"context": "64k", "parallel": "99", "port": "8080", "reserve": 1024, "gone": 1, "access": "lan"})
    assert values["context"] == "64k" and values["access"] == "lan" and values["reserve"] == 1024
    assert values["parallel"] == ""                         # 99 is not a choice: back to the default
    assert values["port"] == 8080                           # text is not a number: back to the default
    assert "gone" not in values
    assert options.clean_values(opts, None) == options.defaults(opts)


def test_template_is_written_once(tmp_path):
    path = tmp_path / "sub" / "options.json"
    options.write_user_template(path)
    first = path.read_text(encoding="utf-8")
    assert json.loads(first)["settings"] == []
    path.write_text('{"settings": [], "remove": ["kv"]}', encoding="utf-8")
    options.write_user_template(path)
    assert "kv" in path.read_text(encoding="utf-8")
    opts, problems = options.load(user_file=path)
    assert problems == [] and "kv" not in [s["key"] for s in opts["settings"]]
