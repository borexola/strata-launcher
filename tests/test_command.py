from strata_launcher import command, config, options


def opts():
    o, problems = options.load(user_file=__import__("pathlib").Path("does-not-exist.json"))
    assert problems == []
    return o


def cfg_for(o, **values):
    c = config.fresh()
    c["strata_command"] = "strata"
    c["values"] = options.clean_values(o, values)
    return c


def test_empty_values_add_nothing():
    o = opts()
    c = cfg_for(o)
    c["source"]["model"] = "q2_0"
    cmd = command.launch_command(c, o)
    assert cmd == ["strata", "launch", "--model", "q2_0", "--tune", "skip", "--access", "local", "--port", "8080",
                   "--save-preset", "Strata Launcher", "--restart", "--yes", "--no-tui"]


def test_every_setting_becomes_its_flags():
    o = opts()
    c = cfg_for(o, context="128k", parallel="4", vision="gpu", drafting="mtp", kv="q4_0", reserve=1024, speed="full", tune="quick",
                gpu="0,1", gpu_roles="0=main,1=helper", access="lan", port=9000)
    c["source"].update(model="iq3_xxs", family="qwen", data_dir="/models")
    flags = command.choice_flags(c, o)
    assert flags[:6] == ["--family", "qwen", "--model", "iq3_xxs", "--data-dir", "/models"]
    for pair in (["--context", "128k"], ["--parallel", "4"], ["--vision", "gpu"], ["--drafting", "mtp"], ["--kv", "q4_0"],
                 ["--set", "vram-reserve-mib=1024"], ["--speed", "full"], ["--tune", "quick"], ["--gpu", "0,1"],
                 ["--gpu-roles", "0=main,1=helper"], ["--access", "lan"], ["--port", "9000"]):
        i = flags.index(pair[0], 6) if pair[0] != "--set" else flags.index("--set")
        assert flags[i:i + 2] == pair


def test_a_typed_context_is_passed_as_typed():
    o = opts()
    c = cfg_for(o, context="48000")
    assert ["--context", "48000"] == command.setting_flags(o["settings"][0], c["values"]["context"])


def test_switch_and_flags_for():
    switch = {"key": "x", "type": "switch", "on": ["--set", "a=on"], "off": ["--set", "a=off"]}
    assert command.setting_flags(switch, True) == ["--set", "a=on"]
    assert command.setting_flags(switch, False) == ["--set", "a=off"]
    choice = {"key": "p", "type": "choice", "flags": ["--parallel", "{value}"], "flags_for": {"auto": []}}
    assert command.setting_flags(choice, "auto") == []
    assert command.setting_flags(choice, "3") == ["--parallel", "3"]
    assert command.setting_flags({"key": "n", "type": "number", "flags": ["--port", "{value}"]}, None) == []


def test_source_kinds():
    assert command.source_flags({"kind": "preset", "preset": "Office", "data_dir": "/x"}) == ["--preset", "Office"]
    assert command.source_flags({"kind": "files", "model_path": "/m/a.gguf"}) == ["--model-path", "/m/a.gguf"]
    assert command.source_flags({"kind": "model", "model": "q2_0"}) == ["--model", "q2_0"]
    assert command.source_problem({"kind": "model", "model": ""})
    assert command.source_problem({"kind": "files", "model_path": ""})
    assert command.source_problem({"kind": "preset", "preset": ""})
    assert command.source_problem({"kind": "preset", "preset": "Office"}) is None


def test_extra_flags_are_split_like_a_terminal():
    assert command.split_flags('--set a=1  --model-path "/my models/x"', windows=False) == ["--set", "a=1", "--model-path", "/my models/x"]
    assert command.split_flags(r'--model-path "C:\My Models\x" --set a=1', windows=True) == ["--model-path", r"C:\My Models\x", "--set", "a=1"]
    assert command.split_flags("", windows=False) == []
    assert command.split_flags('--set "unclosed', windows=False) == ["--set", '"unclosed']


def test_extra_flags_come_last_before_the_fixed_ones():
    o = opts()
    c = cfg_for(o)
    c["source"]["model"] = "q2_0"
    c["extra_flags"] = "--set resident-experts=on"
    cmd = command.launch_command(c, o, windows=False)
    assert cmd[-7:] == ["--set", "resident-experts=on", "--save-preset", "Strata Launcher", "--restart", "--yes", "--no-tui"][-7:]
    assert "resident-experts=on" in cmd


def test_plan_changes_nothing():
    o = opts()
    c = cfg_for(o)
    c["source"]["model"] = "q2_0"
    plan = command.plan_command(c, o)
    assert plan[-3:] == ["--plan", "--output", "json"]
    for word in ("--restart", "--yes", "--save-preset", "--no-tui"):
        assert word not in plan


def test_other_commands_and_printing():
    c = {"strata_command": "/opt/my strata/bin/strata"}
    assert command.stop_command(c)[1:] == ["launch", "--stop"]
    assert command.status_command(c)[1:] == ["launch", "--status", "--output", "json"]
    assert command.key_command(c)[1:] == ["launch", "--show-key"]
    assert command.printable(command.stop_command(c)) == '"/opt/my strata/bin/strata" launch --stop'


def test_strata_update_commands():
    c = {"strata_command": "strata"}
    assert command.update_check_command(c) == ["strata", "update", "--check", "--output", "json"]
    assert command.update_command(c) == ["strata", "update", "--yes"]                       # never stops a model by itself
    assert command.update_command(c, stop_model=True) == ["strata", "update", "--yes", "--stop-model", "--start-again"]
