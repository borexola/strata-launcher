import sys
import threading
import time

import pytest

from strata_launcher import proc

PY = sys.executable


def test_redact_hides_keys():
    assert proc.redact("API key: sk-strata-AbC_123-x ok") == "API key: sk-strata-… ok"
    assert proc.redact("nothing secret") == "nothing secret"


def test_capture_counts_and_returns_output():
    r = proc.Runner()
    code, out = r.capture([PY, "-c", "print('hello')"])
    assert (code, out.strip()) == (0, "hello")
    assert (r.spawned, r.active, r.most_active) == (1, 0, 1)
    assert r.capture(["this-program-does-not-exist-anywhere"])[0] == -1
    assert r.capture([PY, "-c", "import time; time.sleep(5)"], timeout=0.5) == (-1, "")
    assert r.spawned == 3 and r.active == 0


def test_stream_passes_lines_on_and_hides_keys():
    logged, lines = [], []
    r = proc.Runner(logged.append)
    code = r.stream([PY, "-c", "print('one'); print(); print('key sk-strata-secret99'); import sys; sys.exit(3)"], lines.append)
    assert code == 3 and lines == ["one", "key sk-strata-…"]
    assert logged[0].startswith("$ ") and not any("secret99" in x for x in logged)
    plain = []
    r2 = proc.Runner(plain.append)
    r2.stream([PY, "-c", "print('to the log')"])
    assert "to the log" in plain


def test_stream_does_not_wait_for_a_server_that_keeps_the_output_open():
    """`strata launch --no-tui` returns while its server keeps running. The launcher must return too."""
    child = "import time; time.sleep(8)"
    parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}]); print('started')"
    lines = []
    t0 = time.time()
    code = proc.Runner().stream([PY, "-c", parent], lines.append)
    assert code == 0 and lines == ["started"]
    assert time.time() - t0 < 6


def test_the_same_job_never_runs_twice_at_once():
    r = proc.Runner()
    started = threading.Event()
    result = {}

    def long():
        started.set()
        result["first"] = r.capture([PY, "-c", "import time; time.sleep(1.5)"], job="launch")[0]
    t = threading.Thread(target=long)
    t.start()
    started.wait()
    time.sleep(0.3)
    assert r.running("launch")
    with pytest.raises(proc.Busy):
        r.capture([PY, "-c", "print(1)"], job="launch")
    assert r.capture([PY, "-c", "print(1)"], job="other")[0] == 0        # another kind of job may run beside it
    t.join()
    assert result["first"] == 0 and not r.running("launch") and r.active == 0
    assert r.capture([PY, "-c", "print(1)"], job="launch")[0] == 0       # free again afterwards
    assert r.most_active == 2


def test_own_scope_only_where_systemd_can_make_one(monkeypatch):
    cmd = ["strata", "launch"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert proc.own_scope(cmd, which=lambda name: None, usable=lambda: True) == cmd
    scoped = proc.own_scope(cmd, which=lambda name: "/usr/bin/systemd-run", usable=lambda: True)
    assert scoped[0] == "systemd-run" and "--scope" in scoped and scoped[-2:] == cmd and "OOMPolicy=continue" in scoped
    # installed but not usable (a container, a session without a user manager): start the plain way
    assert proc.own_scope(cmd, which=lambda name: "/usr/bin/systemd-run", usable=lambda: False) == cmd
    monkeypatch.setattr(sys, "platform", "win32")
    assert proc.own_scope(cmd, which=lambda name: "C:/x/systemd-run.exe", usable=lambda: True) == cmd


def test_scope_probe_is_asked_once_and_remembered(monkeypatch):
    asked = []

    class FakeRunner:
        def capture(self, cmd, timeout=30, job=None):
            asked.append(cmd)
            return 1, "Failed to connect to bus"
    monkeypatch.setattr(proc, "_scope_works", None)
    assert proc.scope_available(FakeRunner()) is False
    assert proc.scope_available(FakeRunner()) is False
    assert len(asked) == 1 and asked[0][0] == "systemd-run" and asked[0][-1] == "true"
