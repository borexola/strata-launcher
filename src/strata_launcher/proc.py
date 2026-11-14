"""The one place that starts other programs. It counts them, so the self-test can prove nothing runs away.

Rules kept here:
  - every started program is counted (`spawned`) and tracked while it runs (`active`);
  - a named job ("start", "status", "key" ...) never runs twice at the same time;
  - an API key never reaches the log or the window.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time

KEY = re.compile(r"sk-strata-[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    return KEY.sub("sk-strata-…", text)


def no_window_flags() -> int:
    """On Windows: do not flash a console window for every command."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


def own_scope(cmd: list[str], which=shutil.which) -> list[str]:
    """Linux with systemd: run Strata in a scope of its own.

    A program started from the desktop shares one scope with everything it starts. When Linux kills a process
    there for lack of memory, systemd ends the whole scope, the launcher included. In its own scope only Strata ends.
    """
    if sys.platform.startswith("linux") and which("systemd-run"):
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "-p", "OOMPolicy=continue",
                f"--unit=strata-launcher-{int(time.time())}", "--"] + cmd
    return cmd


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
    def capture(self, cmd: list[str], timeout: float = 30, job: str | None = None) -> tuple[int, str]:
        """Runs a short command and returns (exit code, its output). Exit code -1: it could not run or took too long.

        The output is returned as it is (it may hold an API key); it is not logged.
        """
        self._enter(job)
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               text=True, errors="replace", timeout=timeout, creationflags=no_window_flags())
            return p.returncode, p.stdout or ""
        except subprocess.TimeoutExpired:
            return -1, ""
        except OSError as e:
            return -1, str(e)
        finally:
            self._leave(job)

    def stream(self, cmd: list[str], on_line=None, job: str | None = None, timeout: float | None = None) -> int:
        """Runs a command, copies its output line by line to the log and to on_line, returns the exit code.

        It waits for the program to end, not for its output to close: a server that the program leaves running may
        keep the output open, and that must not hang the launcher.
        """
        self._enter(job)
        try:
            self.log("$ " + redact(" ".join(f'"{c}"' if " " in c else c for c in cmd)))
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, errors="replace", creationflags=no_window_flags())
            except OSError as e:
                self.log(f"could not start: {e}")
                if on_line:
                    on_line(f"Could not start it: {e}")
                return -1

            def read():
                try:
                    for line in p.stdout:
                        line = redact(line.rstrip())
                        if not line.strip():
                            continue
                        if on_line:
                            on_line(line.strip())                # the receiver writes it to the log too
                        else:
                            self.log(line)
                except (OSError, ValueError):
                    pass

            reader = threading.Thread(target=read, daemon=True, name="output-reader")
            reader.start()
            try:
                code = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                code = -1
                self.log("stopped: it took too long")
            reader.join(timeout=2.0)
            try:
                p.stdout.close()
            except OSError:
                pass
            return code
        finally:
            self._leave(job)
