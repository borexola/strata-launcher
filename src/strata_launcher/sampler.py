"""Everything live in the window, read once a second in ONE background thread.

Why it is built this way: an earlier panel polled from a GUI timer, the poll re-queued itself, and it started
hundreds of helper programs until the PC froze. Here:

  - one thread with its own one-second loop takes every reading; no GUI timer is involved, so a reading can never
    be queued twice;
  - a reading starts no program: the server is asked over HTTP, the PC is read through psutil, and the graphics
    cards come from ONE `nvidia-smi` that keeps running and prints once a second;
  - the only short programs it ever starts are `strata launch --status` (once when a server appears, to find its
    log) and `strata launch --show-key` (once when the server wants a key), each at most every 30 seconds, never
    two at a time, all counted in proc.Runner;
  - the window is told "something new is ready" at most once until it has collected it, so a slow window cannot
    build up a queue.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

from . import command, strata
from .enginelog import Facts, Tail, summarise
from .metrics import answers_from_requests, short_gpu
from .proc import Busy, Runner, popen

GPU_QUERY = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
ENGINE_NAMES = ("strata-host", "strata-host.exe")
ENGINE_FILES = ("strata", "strata.exe")
STATUS_WAITS = (0, 4, 15, 60)
GIB = 2**30


def http_json(url: str, key: str | None = None, timeout: float = 1.5):
    """(HTTP status, parsed JSON or None). Status 0: no answer."""
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except Exception as e:                                   # no server, a timeout, not JSON: all mean "no reading"
        return getattr(e, "code", 0) or 0, None


def parse_gpu_row(row: str) -> dict | None:
    """One line of `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`."""
    c = [x.strip() for x in row.split(",")]
    if len(c) < 7 or not c[0].isdigit():
        return None

    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    return {"index": int(c[0]), "name": short_gpu(c[1]), "util": num(c[2]), "used": num(c[3]), "total": num(c[4]),
            "temp": num(c[5]), "power": num(c[6])}


def _tie_to_parent_linux():
    """Linux: the system ends the helper program when the thread that started it ends."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)    # PR_SET_PDEATHSIG
    except Exception:
        pass


def _tie_to_parent_windows(process) -> object | None:
    """Windows: put the helper program in a job that the system closes with the launcher, however the launcher ends."""
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle)))
        return job                                           # kept open for the launcher's lifetime
    except Exception:
        return None


class GpuWatch:
    """ONE long-running nvidia-smi. If it is not installed, nothing is started and the cards come from the server."""

    def __init__(self, runner: Runner, program: str = ""):
        self.runner = runner
        self.program = program
        self.gpus: dict[int, dict] = {}
        self.proc = None
        self.job = None
        self.stopping = False
        self.thread = None

    def command(self) -> list[str] | None:
        if self.program == "off":
            return None
        exe = self.program or shutil.which("nvidia-smi")
        if not exe:
            return None
        head = [exe]
        if exe.endswith(".py"):                              # a stand-in written in Python (tests only)
            packaged = "python" if sys.platform.startswith("win") else "python3"     # the packaged program has no Python of its own to lend
            python = os.environ.get("FAKE_STRATA_PYTHON") or (packaged if getattr(sys, "frozen", False) else sys.executable)
            head = [python, exe]
        return head + [f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits", "-l", "1"]

    def start(self):
        if self.command() is None:
            return
        self.thread = threading.Thread(target=self._loop, daemon=True, name="gpu")
        self.thread.start()

    def stop(self):
        self.stopping = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                pass

    def _reap(self):
        """Collects the ended program, so no dead entry stays in the process list."""
        p = self.proc
        if p is None:
            return
        try:
            if self.stopping and p.poll() is None:
                p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=2)
            except Exception:
                pass

    def snapshot(self) -> list[dict]:
        now = time.time()
        return [dict(g) for _, g in sorted(self.gpus.items()) if now - g["at"] < 10]

    def _loop(self):
        while not self.stopping:
            cmd = self.command()
            if cmd is None:
                return
            try:
                with self.runner._lock:
                    self.runner.spawned += 1
                self.proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                                  preexec_fn=_tie_to_parent_linux if sys.platform.startswith("linux") else None)
                if sys.platform.startswith("win"):
                    self.job = _tie_to_parent_windows(self.proc)
                for row in self.proc.stdout:
                    g = parse_gpu_row(row)
                    if g:
                        g["at"] = time.time()
                        self.gpus[g["index"]] = g
                    if self.stopping:
                        break
            except Exception:
                pass
            self._reap()
            for _ in range(60):                              # it ended: try again in 30 s, never at once
                if self.stopping:
                    return
                time.sleep(0.5)


class SystemProbe:
    """Reads the PC through psutil (and /proc, /sys on Linux for the finer figures). Starts no program."""

    def __init__(self):
        self.prev: dict = {}
        self.pid = None
        self.pid_checked = 0.0
        self.port: int | None = None
        self.not_ours: set[int] = set()
        psutil.cpu_percent(None)                             # the first call only sets the starting point

    def _rate(self, name, value, now):
        old = self.prev.get(name)
        self.prev[name] = (value, now)
        if old is None or value is None or old[0] is None or now <= old[1]:
            return None
        return max(0.0, (value - old[0]) / (now - old[1]))

    def system(self, now: float) -> dict:
        s = {}
        try:
            vm, sw = psutil.virtual_memory(), psutil.swap_memory()
            s.update(ram_total=vm.total / GIB, ram_avail=vm.available / GIB, swap_total=sw.total / GIB, swap_free=sw.free / GIB)
            if sys.platform.startswith("linux"):             # psutil counts these in bytes since the PC started
                i, o = self._rate("sin", sw.sin, now), self._rate("sout", sw.sout, now)
                s.update(swap_in_mb_s=i / 1e6 if i is not None else None, swap_out_mb_s=o / 1e6 if o is not None else None)
            s["cpu_pct"] = psutil.cpu_percent(None)
        except Exception:
            pass
        return s

    def set_port(self, port: int | None):
        """The port of the server this launcher looks after. An engine that serves another port is not ours."""
        if port != self.port:
            self.port, self.pid, self.not_ours = port, None, set()

    def find_engine(self, now: float) -> int | None:
        if self.pid:
            try:
                p = psutil.Process(self.pid)
                if p.is_running() and _is_engine(p):
                    return self.pid
            except psutil.Error:
                pass
            self.pid = None
        if now - self.pid_checked < 3.0:
            return None
        self.pid_checked = now
        for p in psutil.process_iter(["name"]):
            try:
                if p.pid in self.not_ours or not _is_engine(p):
                    continue
                port = engine_port(p)
                if self.port and port and port != self.port:
                    self.not_ours.add(p.pid)                 # another Strata server on this PC: leave it alone
                    continue
                self.pid = p.pid
                return self.pid
            except psutil.Error:
                continue
        return None

    def engine(self, now: float) -> dict:
        pid = self.find_engine(now)
        s: dict = {"engine_pid": pid}
        if not pid:
            self.prev.pop("disk", None)
            return s
        try:
            p = psutil.Process(pid)
            mem = p.memory_info()
            if sys.platform.startswith("linux"):
                for line in open(f"/proc/{pid}/status", encoding="ascii", errors="replace"):
                    k, _, v = line.partition(":")
                    if k in ("RssAnon", "RssFile", "RssShmem", "VmSwap"):
                        s[{"RssAnon": "eng_work", "RssFile": "eng_files", "RssShmem": "eng_locked", "VmSwap": "eng_swap"}[k]] = int(v.split()[0]) / 2**20
            else:
                private = getattr(mem, "private", mem.rss)
                s.update(eng_work=min(private, mem.rss) / GIB, eng_files=max(0, mem.rss - private) / GIB, eng_locked=0.0)
            io = p.io_counters()
            rate = self._rate("disk", io.read_bytes, now)
            s.update(disk_mb_s=rate / 1e6 if rate is not None else None, disk_total_gb=io.read_bytes / 1e9)
        except (psutil.Error, OSError, ValueError, AttributeError):
            pass
        return s

    def drives(self, model_dir: str | None) -> list[dict]:
        """Linux: every NVMe drive's temperature from /sys, and which one holds the model files."""
        if not sys.platform.startswith("linux"):
            return []
        holds = None
        if model_dir:
            try:
                best = ""
                for line in open("/proc/mounts", encoding="utf-8", errors="replace"):
                    src, mnt = line.split()[:2]
                    if (model_dir.rstrip("/") + "/").startswith(mnt.rstrip("/") + "/") and len(mnt) > len(best) and src.startswith("/dev/nvme"):
                        best, holds = mnt, re.match(r"/dev/(nvme\d+)", src).group(1)
            except Exception:
                pass
        out = []
        for h in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                if open(h + "/name").read().strip() != "nvme":
                    continue
                ctrl = os.path.basename(os.path.realpath(h + "/device"))

                def rd(n, h=h):
                    return int(open(f"{h}/{n}").read()) / 1000 if os.path.exists(f"{h}/{n}") else None
                temps = [int(open(t).read()) / 1000 for t in glob.glob(h + "/temp*_input")]
                out.append({"id": ctrl, "model": open(f"/sys/class/nvme/{ctrl}/model").read().strip(), "temp": rd("temp1_input"),
                            "hottest": max(temps), "warn": rd("temp1_max"), "crit": rd("temp1_crit"), "holds_model": ctrl == holds})
            except Exception:
                continue
        return out


def port_in(cmdline: list[str]) -> int | None:
    """The number after --port in a command line, or None."""
    for i, word in enumerate(cmdline[:-1]):
        if word == "--port" and cmdline[i + 1].isdigit():
            return int(cmdline[i + 1])
    return None


def engine_port(p) -> int | None:
    """The port an engine's server listens on: Strata's server starts the engine and has --port on its command line."""
    try:
        for parent in [p.parent(), p.parent().parent() if p.parent() else None]:
            if parent is not None:
                port = port_in(parent.cmdline())
                if port:
                    return port
    except (psutil.Error, AttributeError):
        pass
    return None


def _is_engine(p) -> bool:
    name = (p.info["name"] if getattr(p, "info", None) else p.name()) or ""
    name = name.lower()
    if name in ENGINE_NAMES:
        return True
    if name in ENGINE_FILES:
        return "--serve" in p.cmdline()
    return False


class Sampler:
    def __init__(self, runner: Runner, on_ready, probe=None, gpu: GpuWatch | None = None, log=None):
        self.runner = runner
        self.on_ready = on_ready                             # called from the sampler thread: "collect with take()"
        self.probe = probe or SystemProbe()
        self.gpu = gpu if gpu is not None else GpuWatch(runner)
        self.log = log or (lambda text: None)
        self.tail = Tail()
        self.stopping = False
        self.samples = 0
        self.thread = None
        self._lock = threading.Lock()
        self._target = {"url": "", "cfg": None, "engine_log": None, "model_dir": None}
        self._latest = None
        self._records: list = []
        self._first_log = False
        self._notified = False
        self.key, self.key_tried = None, 0.0
        self.status_tries, self.status_next, self.was_up = 0, 0.0, False
        self.found: dict = {}                                # what the status call found: engine_log, model_dir

    # ----- called from the window's thread -----
    def set_target(self, url: str, cfg: dict | None, engine_log: str | None = None, model_dir: str | None = None):
        m = re.search(r":(\d+)$", url or "")
        if hasattr(self.probe, "set_port"):
            self.probe.set_port(int(m.group(1)) if m else None)
        with self._lock:
            changed = url != self._target["url"]
            self._target = {"url": url, "cfg": cfg, "engine_log": engine_log or self._target.get("engine_log"),
                            "model_dir": model_dir}
            if changed:
                self.key, self.key_tried, self.was_up = None, 0.0, False

    def start(self):
        self.gpu.start()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="sampler")
        self.thread.start()

    def stop(self, wait: float = 3.0):
        """Ends the readings. Waits a moment for the reading in progress, so nothing calls into a window that is gone."""
        self.stopping = True
        self.gpu.stop()
        for thread in (self.thread, self.gpu.thread):
            if thread is not None and thread is not threading.current_thread() and thread.is_alive():
                thread.join(wait)

    def take(self):
        """The newest reading and every log line since the last take: (sample or None, lines, first read of a log)."""
        with self._lock:
            s, recs, first = self._latest, self._records, self._first_log
            self._latest, self._records, self._first_log, self._notified = None, [], False, False
        return s, recs, first

    # ----- the sampler thread -----
    def _fetch_key(self, cfg):
        if not cfg or not cfg.get("strata_command") or time.time() - self.key_tried < 30:
            return
        self.key_tried = time.time()
        try:
            _code, out = self.runner.capture(command.key_command(cfg), timeout=20, job="key")
            self.key = strata.parse_key(out)
        except Busy:
            pass

    def _find_files(self, cfg, url):
        """Asks Strata which launch is serving, to learn where its engine log is.

        At most four questions each time a server appears (at once, then 4, 15 and 60 seconds later), never two at once.
        """
        now = time.time()
        if not cfg or not cfg.get("strata_command") or self.status_tries >= len(STATUS_WAITS) or now < self.status_next:
            return
        self.status_tries += 1
        self.status_next = now + (STATUS_WAITS[self.status_tries] if self.status_tries < len(STATUS_WAITS) else 0)
        try:
            _code, out = self.runner.capture(command.status_command(cfg), timeout=20, job="status")
        except Busy:
            return
        m = re.search(r":(\d+)$", url)
        files = strata.launch_files(strata.parse_status(out), int(m.group(1)) if m else None)
        if files.get("engine_log"):
            self.found = files
            self.status_tries = len(STATUS_WAITS)            # found: no more questions until the server changes
            with self._lock:
                self._target["engine_log"] = files["engine_log"]
                if files.get("model_dir") and not self._target.get("model_dir"):
                    self._target["model_dir"] = files["model_dir"]

    def nudge(self):
        """The window calls this after a start: the server may be a new one, so ask again where its log is."""
        self.status_tries, self.status_next, self.found = 0, 0.0, {}

    def sample(self) -> tuple[dict, list, bool]:
        """One reading: (figures, new log lines, whether a log was read for the first time)."""
        now = time.time()
        with self._lock:
            target = dict(self._target)
        url, cfg = target["url"], target["cfg"]
        s = {"at": now, "health": None, "metrics": None, "metrics_denied": False, "gpus": self.gpu.snapshot(), "url": url}
        if url:
            status, h = http_json(url + "/health")
            if status == 200 and isinstance(h, dict):
                s["health"] = h
                status, m = http_json(url + "/metrics", self.key)
                if status in (401, 403):
                    self._fetch_key(cfg)
                    status, m = http_json(url + "/metrics", self.key) if self.key else (401, None)
                s["metrics"] = m if status == 200 and isinstance(m, dict) else None
                s["metrics_denied"] = status in (401, 403)
        up = bool(s["health"])
        if up and not self.was_up:
            self.nudge()
        if up and not self.found:
            self._find_files(cfg, url)
            with self._lock:
                target = dict(self._target)
        self.was_up = up
        s.update(self.probe.system(now))
        s.update(self.probe.engine(now))
        s["drives"] = self.probe.drives(target.get("model_dir"))
        s["engine_log"] = target.get("engine_log")
        self.tail.follow(target.get("engine_log"))
        records, first = self.tail.read()
        # Copies made here, in the sampler thread, so the window never reads the log reader while it changes.
        s["run"] = Facts(self.tail.perf)
        s["answers"] = self.tail.perf.summary() or summarise(answers_from_requests((s["metrics"] or {}).get("requests")))
        s["answers_from_log"] = bool(self.tail.perf.answers)
        return s, records, first

    def _loop(self):
        while not self.stopping:
            t0 = time.time()
            try:
                s, records, first = self.sample()
                self.samples += 1
                with self._lock:
                    self._latest = s
                    self._records += records
                    del self._records[:-6000]
                    self._first_log = self._first_log or first
                    tell = not self._notified
                    self._notified = True
                if tell and not self.stopping:
                    self.on_ready()
            except Exception as e:                           # one bad reading must not end the readings
                self.log(f"reading failed: {e!r}")
            wait = max(0.25, 1.0 - (time.time() - t0))
            while wait > 0 and not self.stopping:
                time.sleep(min(0.1, wait))
                wait -= 0.1
