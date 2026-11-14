import json
import os
import subprocess
import sys
import time

import pytest

from strata_launcher import command, config, options, strata
from strata_launcher.proc import Runner
from strata_launcher.sampler import GpuWatch, Sampler, SystemProbe, http_json, parse_gpu_row, port_in


class QuietProbe:
    def system(self, now):
        return {"ram_total": 32.0, "ram_avail": 20.0, "swap_total": 4.0, "swap_free": 4.0, "cpu_pct": 5.0}

    def engine(self, now):
        return {"engine_pid": None}

    def drives(self, model_dir):
        return []


def wait_for(check, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


def make_cfg(env, access="local"):
    opts, _ = options.load()
    cfg = config.fresh()
    cfg["strata_command"] = env["strata"]
    cfg["source"]["model"] = "demo-q4"
    cfg["values"] = options.clean_values(opts, {"port": env["port"], "access": access, "context": "32k"})
    return cfg, opts


def test_parse_gpu_row():
    g = parse_gpu_row("1, NVIDIA GeForce Example 16GB, 37, 6400, 16384, 49, 70.25")
    assert g == {"index": 1, "name": "Example 16GB", "util": 37.0, "used": 6400.0, "total": 16384.0, "temp": 49.0, "power": 70.25}
    assert parse_gpu_row("0, Card, [N/A], 100, 8192, 40, [N/A]")["power"] is None
    assert parse_gpu_row("No devices were found") is None and parse_gpu_row("") is None


def test_port_in_a_command_line():
    assert port_in(["python", "-m", "serve.server", "--config", "/x.json", "--host", "0.0.0.0", "--port", "8081", "--parallel", "1"]) == 8081
    assert port_in(["python", "-m", "something"]) is None and port_in(["x", "--port"]) is None and port_in(["x", "--port", "abc"]) is None


def test_probe_forgets_its_engine_when_the_port_changes():
    probe = SystemProbe()
    probe.pid, probe.not_ours = 123, {5}
    probe.set_port(8080)
    assert probe.pid is None and probe.not_ours == set() and probe.port == 8080
    probe.not_ours.add(7)
    probe.set_port(8080)
    assert probe.not_ours == {7}                              # the same port again changes nothing


def test_no_answer_is_status_zero(fake_env):
    assert http_json(fake_env["url"] + "/health", timeout=0.5) == (0, None)


def test_system_probe_reads_this_pc_without_starting_anything():
    probe = SystemProbe()
    s = probe.system(time.time())
    assert s["ram_total"] > 0 and 0 <= s["ram_avail"] <= s["ram_total"] and 0 <= s["cpu_pct"] <= 100
    assert isinstance(probe.drives(None), list)
    assert "engine_pid" in probe.engine(time.time())


def test_card_reader_is_one_program_that_ends_with_stop(fake_env):
    runner = Runner()
    gpu = GpuWatch(runner, fake_env["nvidia_smi"])
    gpu.start()
    assert wait_for(lambda: len(gpu.snapshot()) == 2)
    time.sleep(1.5)
    assert runner.spawned == 1                                # still the same one program after more readings
    assert gpu.snapshot()[0]["name"] == "Demo Card A 24GB"
    gpu.stop()
    assert wait_for(lambda: gpu.proc.poll() is not None, 5)
    assert GpuWatch(runner, "off").command() is None


def test_sampler_against_the_stand_in(fake_env):
    """Start the stand-in the way the Start button does, and let the sampler find the server, the key and the log."""
    cfg, opts = make_cfg(fake_env, access="lan")
    runner = Runner()
    lines = []
    assert runner.stream(command.launch_command(cfg, opts), lines.append, job="launch") == 0
    assert any("Serving at" in x for x in lines) and not any("stand-in-0000" in x for x in lines)      # the key line is cut
    told = []
    sampler = Sampler(runner, lambda: told.append(1), probe=QuietProbe(), gpu=GpuWatch(runner, "off"))
    sampler.set_target(fake_env["url"], cfg)
    try:
        before = runner.spawned
        sampler.start()
        seen = {"sample": None, "records": []}

        def collect():
            s, records, _first = sampler.take()
            seen["records"] += records
            if s is not None:
                seen["sample"] = s
            return seen["sample"] is not None and seen["sample"].get("metrics") and any(k == "start" for k, _t in seen["records"])
        assert wait_for(collect, 20)
        s = seen["sample"]
        assert s["health"]["api_key"] is True and s["metrics"]["engine"]["model"] == "demo-model-q4"
        assert s["engine_log"].endswith("strata-launcher.engine.log") and s["run"].context == 32768
        assert sampler.key and sampler.key.startswith("sk-strata-")
        time.sleep(3)
        sampler.take()
        assert runner.spawned - before == 2                   # one status question and one key question, then no more
        assert len(told) <= sampler.samples                   # never told more often than there were readings
    finally:
        sampler.stop()
        assert runner.stream(command.stop_command(cfg), job="launch") == 0
    assert http_json(fake_env["url"] + "/health", timeout=0.5)[0] == 0


def test_window_is_told_once_until_it_collects(fake_env):
    told = []
    sampler = Sampler(Runner(), lambda: told.append(1), probe=QuietProbe(), gpu=GpuWatch(Runner(), "off"))
    sampler.set_target(fake_env["url"], None)
    sampler.start()
    try:
        assert wait_for(lambda: sampler.samples >= 3, 10)
        assert len(told) == 1                                 # three readings, one "come and get it"
        s, _records, _first = sampler.take()
        assert s is not None and s["health"] is None
        assert wait_for(lambda: len(told) == 2, 5)
    finally:
        sampler.stop()


def test_status_is_asked_a_few_times_then_never_again(fake_env, monkeypatch):
    """A server that `strata launch --status` does not know (started some other way) must not be asked about for ever."""
    from strata_launcher import sampler as sampler_module
    monkeypatch.setattr(sampler_module, "STATUS_WAITS", (0, 0.5, 0.5, 0.5))
    stub = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(fake_env["nvidia_smi"]), "stub_server.py"), "--port", str(fake_env["port"])])
    try:
        assert wait_for(lambda: http_json(fake_env["url"] + "/health")[0] == 200)
        cfg, _opts = make_cfg(fake_env)
        runner = Runner()
        sampler = Sampler(runner, lambda: None, probe=QuietProbe(), gpu=GpuWatch(runner, "off"))
        sampler.set_target(fake_env["url"], cfg)
        sampler.start()
        assert wait_for(lambda: sampler.samples >= 7, 15)
        sampler.stop()
        assert runner.spawned == 4 and runner.most_active == 1
    finally:
        stub.terminate()
        stub.wait()


def test_fake_strata_refuses_and_plans(fake_env):
    """The stand-in behaves like Strata where the launcher depends on it."""
    cfg, opts = make_cfg(fake_env)
    runner = Runner()
    code, out = runner.capture(command.status_command(cfg))
    assert code == 0 and strata.parse_status(out) == []
    plan = strata.parse_plan(runner.capture(command.plan_command(cfg, opts))[1])
    assert plan["ok"] and plan["download_gb"] == 0
    cfg["source"]["model"] = "not-installed"
    assert strata.parse_plan(runner.capture(command.plan_command(cfg, opts))[1])["download_gb"] == pytest.approx(12.3)
    assert strata.parse_version(runner.capture(command.version_command(cfg["strata_command"]))[1]) == "0.0.0"
    models = strata.parse_models(runner.capture(command.models_command(cfg))[1])
    assert [m["id"] for m in models] == ["demo-q4", "demo-q2"]
    presets = strata.parse_presets(runner.capture(command.presets_command(cfg))[1])
    assert presets and json.dumps(presets)
