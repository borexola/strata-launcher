"""The one place that starts other programs. It counts them, so the self-test can prove nothing runs away.

Rules kept here:
  - every started program is counted (`spawned`) and tracked while it runs (`active`);
  - a named job ("start", "status", "key" ...) never runs twice at the same time;
  - an API key never reaches the log or the window.
"""

from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

KEY = re.compile(r"sk-strata-[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    return KEY.sub("sk-strata-…", text)


def no_window_flags() -> int:
    """On Windows: do not flash a console window for every command."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


_scope_works: bool | None = None


def scope_available(runner=None) -> bool:
    """Linux: can this session start a systemd scope of its own? Asked once with a command that does nothing, then remembered.

    systemd-run can be installed and still not usable: in a container, or in a session without a user manager.
    """
    global _scope_works
    if _scope_works is None:
        probe = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", "true"]
        try:
            if runner is not None:
                code, _out = runner.capture(probe, timeout=8)
            else:
                code = subprocess.run(probe, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8).returncode
            _scope_works = code == 0
        except (OSError, subprocess.SubprocessError):
            _scope_works = False
    return _scope_works


def own_scope(cmd: list[str], which=shutil.which, usable=scope_available, runner=None) -> list[str]:
    """Linux with systemd: run Strata in a scope of its own.

    A program started from the desktop shares one scope with everything it starts. When Linux kills a process
    there for lack of memory, systemd ends the whole scope, the launcher included. In its own scope only Strata ends.
    Where a scope cannot be made, the command is returned as it is and Strata starts the plain way.
    """
    if sys.platform.startswith("linux") and which("systemd-run") and (usable(runner) if usable is scope_available else usable()):
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "-p", "OOMPolicy=continue",
                f"--unit=strata-launcher-{int(time.time())}", "--"] + cmd
    return cmd


_spawn_lock = threading.Lock()


def clean_frozen_environment() -> None:
    """Linux, packaged program only: give the programs we start the library path the person's system had.

    The packaged program runs with LD_LIBRARY_PATH pointing into its own folder. Strata and the web browser must
    not inherit that, or they would load this program's copies of system libraries instead of their own.
    Changing the variable here does not affect the launcher itself: its libraries are loaded already.
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
        return
    original = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if original is not None:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen for every program the launcher starts.

    Windows, packaged program only: the packaged program tells Windows to look for DLLs in its own folder first, and
    a program started from it would inherit that. So for the moment of the start the setting is taken away, and the
    started program (Strata, with its own Python) finds its own DLLs.
    """
    kwargs.setdefault("creationflags", no_window_flags())
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("win")):
        return subprocess.Popen(cmd, **kwargs)
    import ctypes
    kernel32 = ctypes.windll.kernel32
    with _spawn_lock:
        kernel32.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(cmd, **kwargs)
        finally:
            kernel32.SetDllDirectoryW(getattr(sys, "_MEIPASS", None))


def decode(data: bytes) -> str:
    """Program output as text: UTF-8 when it is that, else the system's own encoding, never an error."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False) or "utf-8", "replace")


class Busy(Exception):
    """The same job is already running."""


class Runner:
    def __init__(self, log=None):
        self.log = log or (lambda text: None)
        self.spawned = 0
        self.active = 0
        self.most_active = 0
        self._lock = threading.Lock()
        self._jobs: set[str] = set()

    # ----- bookkeeping -----
    def _enter(self, job: str | None):
        with self._lock:
            if job:
                if job in self._jobs:
                    raise Busy(job)
                self._jobs.add(job)
            self.spawned += 1
            self.active += 1
            self.most_active = max(self.most_active, self.active)

    def _leave(self, job: str | None):
        with self._lock:
            self.active -= 1
            if job:
                self._jobs.discard(job)

    def running(self, job: str) -> bool:
        with self._lock:
            return job in self._jobs

    # ----- running things -----
    def _run(self, cmd: list[str], timeout: float | None, on_line=None) -> tuple[int, str]:
        """Runs cmd with its output going to a temporary file, and reads that file while it runs.

        A file, not a pipe: a server that the command leaves running may keep the output open for days. With a pipe
        the launcher would wait for it; with a file it only waits for the command itself to end.
        Returns (exit code, the whole output). Exit code -1: it took too long. -2: it could not start (the output
        is then the reason).
        """
        fd, path = tempfile.mkstemp(prefix="strata-launcher-", suffix=".out")
        text: list[str] = []
        try:
            with os.fdopen(fd, "wb") as out:
                try:
                    p = popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                except OSError as e:
                    return -2, str(e)
            started, rest, code = time.time(), b"", None
            with open(path, "rb") as f:
                while True:
                    ended = p.poll() is not None
                    chunk = f.read()
                    if chunk:
                        *lines, rest = (rest + chunk).split(b"\n")
                        for raw in lines:
                            line = decode(raw).rstrip("\r")
                            text.append(line)
                            if on_line:
                                on_line(line)
                    if ended:
                        code = p.returncode
                        break
                    if timeout is not None and time.time() - started > timeout:
                        p.kill()
                        p.wait()
                        code = -1
                        break
                    if not chunk:
                        time.sleep(0.05)
            if rest:
                line = decode(rest).rstrip("\r")
                text.append(line)
                if on_line:
                    on_line(line)
            return code, "\n".join(text)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass                                         # still open in a program that kept running: left in the temp folder

    def capture(self, cmd: list[str], timeout: float = 30, job: str | None = None) -> tuple[int, str]:
        """Runs a short command and returns (exit code, its output). Exit code -1: it could not run or took too long.

        The output is returned as it is (it may hold an API key); it is not logged.
        """
        self._enter(job)
        try:
            code, text = self._run(cmd, timeout)
            return (-1, text) if code == -2 else (code, text)
        finally:
            self._leave(job)

    def stream(self, cmd: list[str], on_line=None, job: str | None = None, timeout: float | None = None) -> int:
        """Runs a command and passes its output on line by line (to on_line, else to the log). Returns the exit code.

        API keys are cut out of every line first.
        """
        self._enter(job)
        try:
            self.log("$ " + redact(" ".join(f'"{c}"' if " " in c else c for c in cmd)))

            def each(line: str):
                line = redact(line).strip()
                if not line:
                    return
                if on_line:
                    on_line(line)                            # the receiver writes it to the log too
                else:
                    self.log(line)
            code, text = self._run(cmd, timeout, each)
            if code == -2:
                self.log(f"could not start: {redact(text)}")
                if on_line:
                    on_line(f"Could not start it: {redact(text)}")
                return -1
            if code == -1:
                self.log("it took too long and was stopped")
            return code
        finally:
            self._leave(job)
