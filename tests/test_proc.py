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
    full = proc.SCOPE + proc.KEEP_ON_OOM
    monkeypatch.setattr(sys, "platform", "linux")
    assert proc.own_scope(cmd, which=lambda name: None, head=lambda runner: full) == cmd
    scoped = proc.own_scope(cmd, which=lambda name: "/usr/bin/systemd-run", head=lambda runner: full)
    assert scoped[0] == "systemd-run" and "--scope" in scoped and scoped[-2:] == cmd and "OOMPolicy=continue" in scoped
    older = proc.own_scope(cmd, which=lambda name: "/usr/bin/systemd-run", head=lambda runner: proc.SCOPE)
    assert older[0] == "systemd-run" and "OOMPolicy=continue" not in older and older[-2:] == cmd
    # installed but not usable (a container, a session without a user manager): start the plain way
    assert proc.own_scope(cmd, which=lambda name: "/usr/bin/systemd-run", head=lambda runner: None) == cmd
    monkeypatch.setattr(sys, "platform", "win32")
    assert proc.own_scope(cmd, which=lambda name: "C:/x/systemd-run.exe", head=lambda runner: full) == cmd


class ScopeRunner:
    """Stands in for systemd-run: `works` says which kinds of probe succeed."""

    def __init__(self, works):
        self.works, self.asked = works, []

    def capture(self, cmd, timeout=30, job=None):
        self.asked.append(cmd)
        kind = "with-policy" if "OOMPolicy=continue" in cmd else "plain"
        return (0, "") if kind in self.works else (1, "Unknown assignment: OOMPolicy=continue")


def fresh_scope(monkeypatch):
    monkeypatch.setattr(proc, "_scope_asked", False)
    monkeypatch.setattr(proc, "_scope_head", None)


def test_scope_probe_new_systemd(monkeypatch):
    fresh_scope(monkeypatch)
    r = ScopeRunner({"with-policy", "plain"})
    assert proc.scope_head(r) == proc.SCOPE + proc.KEEP_ON_OOM
    assert proc.scope_head(r) == proc.SCOPE + proc.KEEP_ON_OOM
    assert len(r.asked) == 1 and r.asked[0][-1] == "true"                # asked once, remembered


def test_scope_probe_older_systemd_without_the_policy(monkeypatch):
    fresh_scope(monkeypatch)
    r = ScopeRunner({"plain"})
    assert proc.scope_head(r) == proc.SCOPE
    assert len(r.asked) == 2


def test_scope_probe_where_no_scope_can_be_made(monkeypatch):
    fresh_scope(monkeypatch)
    r = ScopeRunner(set())
    assert proc.scope_head(r) is None and proc.scope_head(r) is None
    assert len(r.asked) == 2
