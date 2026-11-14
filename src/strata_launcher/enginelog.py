"""Reads Strata's engine log and turns its lines into plain sentences and figures.

The engine writes one group of lines when it starts and one when an answer ends. The patterns below match the
wording of engine 0.1.x. A line that matches nothing is kept as a "detail" line, so a new engine version loses
nothing: it only gets fewer plain sentences.
"""

from __future__ import annotations

import os
import re

R_START = re.compile(r"^\[strata\] (\S+) (\S+) engine started: (.*)")
R_REQ = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\), "
                   r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)")
R_HIT = re.compile(r"decode expert cache hit rate: ([\d.]+)% .*?\(([\d.]+)% of all")
R_HELP = re.compile(r"strata serve: \w+1: (\d+) expert entries, .*?(\d+) ms waiting for it")
R_FILEIO = re.compile(r"file tier I/O this request: the OS read ([\d.]+) MB from storage \((\d+) major faults\) "
                      r"for ([\d.]+) MB of expert reads")
R_CACHE = re.compile(r"strata generate: expert cache (\d+) slots, ([\d.]+) GiB of VRAM")
R_CARD2 = re.compile(r"strata generate: \w+1: (\d+) additional experts, ([\d.]+) GiB")
R_RAM = re.compile(r"resident RAM mode: ([\d.]+) GiB of experts in RAM")
R_SLOTS = re.compile(r"the slot sessions take ([\d.]+) GiB of VRAM")
R_FREE = re.compile(r"strata serve: (\d+) MiB of VRAM free with everything loaded")
R_UP = re.compile(r"session is up \(engine ([\d.]+)\)")
R_TOTAL = re.compile(r"prefill plan inputs: ([\d.]+) GiB experts")
R_PLAN = re.compile(r"prefill plan: .*?file set ([\d.]+) GiB (fits|does not fit) the page cache ([\d.]+) GiB")
R_PCIE = re.compile(r"PCIe probe: ([\d.]+) GB/s")
R_BAD = re.compile(r"\b(error|failed|fatal|killed|out of memory|cannot|refus)", re.I)

KINDS = ("start", "key", "answer", "warn", "error", "detail", "launcher", "live")


class Perf:
    """What the engine log says about the current run: where the model sits, and every answer's speed.

    feed(line) takes one log line and returns the lines to show, as (kind, text):
      start    a new engine start          key      a fact worth reading, in plain words
      answer   one finished answer         warn     a warning
      error    something went wrong        detail   the engine's own wording
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = self.engine = self.context = self.slots = None
        self.gpu0_gib = self.gpu0_n = self.gpu1_gib = self.gpu1_n = self.ram_gib = self.total_gib = self.slots_gib = None
        self.vram_free = self.pcie = self.file_set = self.file_fits = None
        self.answers: list[dict] = []
        self.checks = 0

    def feed(self, line: str) -> list[tuple[str, str]]:
        out = [("detail", line)]
        m = R_START.search(line)
        if m:
            self.reset()
            self.started = f"{m.group(1)} {m.group(2)}"
            c, b = re.search(r"--max-context (\d+)", m.group(3)), re.search(r"--batch (\d+)", m.group(3))
            self.context, self.slots = (int(c.group(1)) if c else None), (int(b.group(1)) if b else 1)
            text = f"──────── Engine started {m.group(2)}"
            if self.context:
                text += f" · context {self.context // 1024}K · {self.slots} request slot{'s' if self.slots != 1 else ''}"
            return [("start", text + " ────────"), ("detail", line)]
        m = R_REQ.search(line)
        if m:
            prompt, reused, read, _read_ms, read_tps, gen, gen_ms, tps, acc, of = m.groups()
            a = {"prompt": int(prompt), "reused": int(reused), "read": int(read), "read_tps": float(read_tps), "out": int(gen),
                 "ms": int(gen_ms), "tps": float(tps), "acc": int(acc), "of": int(of), "hit": None, "other": None,
                 "wait_ms": None, "disk_mb": None, "faults": None, "files_mb": None}
            if a["out"] <= 1:                                # Strata's own watchdog asks for one token now and then
                self.checks += 1
                return out
            self.answers.append(a)
            del self.answers[:-500]
            return [("answer", answer_text(a))] + out
        last = self.answers[-1] if self.answers else None
        m = R_HIT.search(line)
        if m and last and last["hit"] is None:
            last.update(hit=float(m.group(1)), other=float(m.group(2)))
            return out
        m = R_HELP.search(line)
        if m and last and last["wait_ms"] is None:
            last["wait_ms"] = int(m.group(2))
            return out
        m = R_FILEIO.search(line)
        if m and last and last["disk_mb"] is None:
            last.update(disk_mb=float(m.group(1)), faults=int(m.group(2)), files_mb=float(m.group(3)))
            # Parts of the model that are in no locked copy come from the model files through the system's file cache.
            if last["disk_mb"] >= 500:
                need, disk = last["files_mb"] / 1000, last["disk_mb"] / 1000
                cached = max(0.0, 1 - disk / need) if need > 0 else 0.0
                return [("warn", f"   ↳ {disk:.1f} GB came from the drive for that answer. The engine read {need:.1f} GB from the "
                                 f"model files in all; the system served {100 * cached:.0f}% of that from memory.")] + out
            return out
        m = R_CACHE.search(line)
        if m:
            self.gpu0_n, self.gpu0_gib = int(m.group(1)), float(m.group(2))
            return [("key", f"1st card holds {self.gpu0_gib:.2f} GiB of the model ({self.gpu0_n:,} parts)")] + out
        m = R_CARD2.search(line)
        if m:
            self.gpu1_n, self.gpu1_gib = int(m.group(1)), float(m.group(2))
            return [("key", f"2nd card holds {self.gpu1_gib:.2f} GiB of the model ({self.gpu1_n:,} parts)")] + out
        m = R_RAM.search(line)
        if m:
            self.ram_gib = float(m.group(1))
            return [("key", f"RAM holds {self.ram_gib:.2f} GiB of the model, locked in place")] + out
        m = R_SLOTS.search(line)
        if m:
            self.slots_gib = float(m.group(1))
            return out
        m = R_PLAN.search(line)
        if m:
            self.file_set, self.file_fits = float(m.group(1)), m.group(2) == "fits"
            if not self.file_fits:
                return [("warn", f"{self.file_set:.1f} GiB of the model must be read from the model files while prompts are read, "
                                 f"and only {float(m.group(3)):.1f} GiB of RAM is free to keep them: expect drive reads and "
                                 "slower prompt reading")] + out
            return out
        m = R_FREE.search(line)
        if m:
            self.vram_free = int(m.group(1))
            kind = "warn" if self.vram_free < 512 else "key"
            return [(kind, f"Loaded. {self.vram_free:,} MiB of graphics memory is free on the 1st card"
                           + (" - that is low" if kind == "warn" else ""))] + out
        m = R_UP.search(line)
        if m:
            self.engine = m.group(1)
            return out
        m = R_TOTAL.search(line)
        if m:
            self.total_gib = float(m.group(1))
            return out
        m = R_PCIE.search(line)
        if m:
            self.pcie = float(m.group(1))
            return out
        if "WARNING" in line:
            return [("warn", line)]
        if R_BAD.search(line.replace("no error", "")) and "turns it off" not in line:
            return [("error", line)]
        return out

    def summary(self) -> dict | None:
        return summarise(self.answers)


def answer_text(a: dict) -> str:
    text = f"✔ Answer: {a['out']:,} tokens at {a['tps']:.1f} tok/s in {a['ms'] / 1000:.0f} s  ·  prompt {a['prompt']:,} tokens"
    if a.get("reused"):
        text += f" ({a['reused']:,} reused)"
    if a.get("read", 0) >= 200 and a.get("read_tps"):
        text += f", read at {a['read_tps']:,.0f} tok/s"
    if a.get("of"):
        text += f"  ·  drafts {100 * a['acc'] // a['of']}% accepted"
    return text


def summarise(answers: list[dict]) -> dict | None:
    """Figures over a list of finished answers, or None when there are none."""
    rs = answers
    if not rs:
        return None
    ms, toks = sum(a["ms"] for a in rs), sum(a["out"] for a in rs)
    long_reads = [a for a in rs if a.get("read", 0) >= 200 and a.get("read_tps")]
    last = rs[-1]
    return {"last": last, "count": len(rs), "tokens": toks, "avg": toks * 1000.0 / ms if ms else 0.0,
            "best": max(a["tps"] for a in rs), "read": long_reads[-1]["read_tps"] if long_reads else None,
            "drafts": 100.0 * last["acc"] / last["of"] if last.get("of") else None, "hit": last.get("hit"),
            "other": last.get("other"), "disk_gb": sum((a.get("disk_mb") or 0) for a in rs) / 1000.0,
            "history": [a["tps"] for a in rs[-40:]]}


def _lines(data: bytes) -> list[str]:
    return [ln for ln in data.decode("utf-8", "replace").replace("\x00", "").split("\n") if ln.strip() and "sk-strata-" not in ln]


def read_run(path: str, perf: Perf, limit: int = 4 * 2**20) -> tuple[list[tuple[str, str]], int | None]:
    """Feeds the end of the engine log to perf, from the last engine start in it. Returns (lines to show, position)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return [], None
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], size
    text = data[:cut + 1]
    at = text.rfind(b" engine started: ")
    at = text.rfind(b"\n", 0, at) + 1 if at >= 0 else 0      # the start of the line that says "engine started"
    records: list[tuple[str, str]] = []
    for line in _lines(text[at:]):
        records += perf.feed(line)
    return records, max(0, size - limit) + cut + 1


class Tail:
    """Follows one engine log: the current run first, then whatever is added, in whole lines."""

    def __init__(self, perf: Perf | None = None):
        self.perf = perf or Perf()
        self.path: str | None = None
        self.pos: int | None = None

    def follow(self, path: str | None):
        if path != self.path:
            self.path, self.pos = path, None
            self.perf.reset()

    def read(self, most: int = 2**20) -> tuple[list[tuple[str, str]], bool]:
        """Returns (new lines to show, whether this was the first read of this file)."""
        if not self.path:
            return [], False
        if self.pos is None:
            records, self.pos = read_run(self.path, self.perf)
            return records[-1500:], self.pos is not None
        try:
            size = os.path.getsize(self.path)
            if size < self.pos:                              # the file was replaced by a shorter one
                self.pos = 0
            if size == self.pos:
                return [], False
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(most)
        except OSError:
            return [], False
        cut = data.rfind(b"\n")
        if cut < 0:
            return [], False
        self.pos += cut + 1
        records: list[tuple[str, str]] = []
        for line in _lines(data[:cut + 1]):
            records += self.perf.feed(line)
        return records, False
