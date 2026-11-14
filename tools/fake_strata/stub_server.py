#!/usr/bin/env python3
"""A stand-in for Strata's server, for tests and screenshots. It loads no model and every figure is made up.

It serves /health and /metrics on 127.0.0.1 and pretends to answer one request after another:
idle -> reading the prompt -> thinking -> answering -> idle. Each finished answer is added to /metrics and
written to the engine log in the wording Strata's engine uses.

  stub_server.py --port 18080 --log engine.log [--model NAME] [--context 32768] [--slots 2] [--key KEY]
                 [--seed-answers 12] [--hold answering] [--helper]
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CYCLE = (("idle", 6.0), ("reading the prompt", 3.0), ("thinking", 3.0), ("answering", 14.0))
PROMPT_TOKENS = 6400
SPEED = 96.0                                                 # made-up tokens per second


class World:
    def __init__(self, a):
        self.a = a
        self.t0 = time.time()
        self.rng = random.Random(7)
        self.requests: list[dict] = []
        self.totals = {"since": self.t0, "requests": 0, "prompt_tokens": 0, "reused": 0, "output_tokens": 0,
                       "prompt_ms": 0.0, "decode_ms": 0.0, "drafts_offered": 0, "drafts_accepted": 0}
        self.cycle_no = -1
        self.lock = threading.Lock()
        self.log_lines(self.start_lines())
        for i in range(a.seed_answers):
            self.finish(when=self.t0 - (a.seed_answers - i) * 40)

    # ----- the engine log -----
    def log_lines(self, lines):
        if not self.a.log:
            return
        with open(self.a.log, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def start_lines(self):
        a = self.a
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.t0))
        lines = [f"[strata] {stamp} engine started: stand-in-engine --serve --pack /stand-in/pack --max-context {a.context} --batch {a.slots}",
                 "strata generate: PCIe probe: 24.0 GB/s host->device (best of 24.0 24.0 24.0 24.0) -> pcie_frac 0.55 (default 0.55)",
                 "strata generate: expert cache 5200 slots, 9.80 GiB of VRAM; policy is"]
        if a.helper:
            lines.append("strata generate: CUDA1: 3100 additional experts, 5.90 GiB; results return through pinned host rows")
        lines += ["strata generate: resident RAM mode: 6.40 GiB of experts in RAM (page-locked), 5200 in the GPU cache",
                  f"strata generate: --batch {a.slots}: the slot sessions take {1.6 * a.slots:.2f} GiB of VRAM on CUDA0 that the expert cache would otherwise hold",
                  "strata: prefill plan inputs: 22.1 GiB experts, 9.8 GiB in the GPU caches, 6.4 GiB RAM copy",
                  "strata generate: session is up (engine 0.0.0)",
                  "strata serve: 2140 MiB of VRAM free with everything loaded"]
        return lines

    def finish(self, when=None):
        """One finished answer: into /metrics and into the log."""
        r = self.rng
        tokens = r.randint(180, 1900)
        tps = round(SPEED * r.uniform(0.82, 1.12), 1)
        prompt = r.choice((420, 980, 2300, PROMPT_TOKENS, 9100))
        reused = r.choice((0, 0, prompt // 2, prompt - 40))
        read = prompt - reused
        read_tps = round(r.uniform(900, 2400), 1)
        offered = tokens // 3
        accepted = int(offered * r.uniform(0.45, 0.8))
        decode_ms = int(tokens * 1000 / tps)
        prompt_ms = int(read * 1000 / read_tps)
        hit = round(r.uniform(0.93, 0.995), 3)
        with self.lock:
            self.requests.append({"id": f"{r.getrandbits(48):012x}", "time": when or time.time(), "duration_s": round((decode_ms + prompt_ms) / 1000, 1),
                                  "finish": "stop", "prompt_tokens": prompt, "reused": reused, "output_tokens": tokens,
                                  "prompt_total": prompt, "prompt_read": read, "prompt_ms": float(prompt_ms), "decode_ms": float(decode_ms),
                                  "decode_tok_s": tps, "hit_rate": hit, "pcie_share": round(1 - hit, 3), "ram_blobs": 0,
                                  "file_blobs": 0, "file_mb": 0, "drafts_offered": offered, "drafts_accepted": accepted})
            del self.requests[:-50]
            t = self.totals
            t["requests"] += 1
            t["prompt_tokens"] += prompt
            t["reused"] += reused
            t["output_tokens"] += tokens
            t["prompt_ms"] += prompt_ms
            t["decode_ms"] += decode_ms
            t["drafts_offered"] += offered
            t["drafts_accepted"] += accepted
        lookups = tokens * 3
        self.log_lines([
            f"strata serve: prompt {prompt} tokens = {reused} reused + {read} read in {prompt_ms} ms ({read_tps} tok/s), {tokens} generated in {decode_ms} ms ({tps} tok/s), drafts accepted {accepted} of {offered}, 1 checkpoints",
            f"strata serve: decode expert cache hit rate: {100 * hit:.1f}% ({int(lookups * hit)} hits / {lookups} lookups); {int(lookups * (1 - hit))} more read by the GPU over PCIe or from another GPU ({100 * (1 - hit):.1f}% of all {lookups} routed)",
            "strata serve: file tier I/O this request: the OS read 0.4 MB from storage (0 major faults) for 0.0 MB of expert reads",
        ])

    # ----- what the server is doing now -----
    def phase(self):
        """(phase name, seconds into the phase, seconds the phase lasts, cycle number)."""
        if self.a.hold:                                      # stay in one phase, with figures that still move
            return self.a.hold, 2.0 + (time.time() - self.t0) % 10.0, 14.0, 0
        el = time.time() - self.t0
        total = sum(d for _, d in CYCLE)
        n, at = int(el // total), el % total
        for name, dur in CYCLE:
            if at < dur:
                return name, at, dur, n
            at -= dur
        return "idle", 0.0, 1.0, n

    def tick(self):
        """Called before every answer: when a cycle has ended, its answer is recorded once."""
        _name, _at, _dur, n = self.phase()
        if self.a.hold:
            return
        with self.lock:
            due = n > self.cycle_no and self.cycle_no >= 0
            first = self.cycle_no < 0
            self.cycle_no = max(self.cycle_no, n)
        if due and not first:
            self.finish()

    def live(self):
        name, at, dur, _n = self.phase()
        slots = self.a.slots
        if name == "idle":
            return {"state": "idle", "queued": 0, "phase": None, "prompt_tokens": None, "prompt_read": None, "prompt_total": None,
                    "generated": None, "max_tokens": None, "elapsed_s": None, "tok_s": None, "tok_s_mean": None, "parallel": slots, "running": 0}
        lead = {"reading the prompt": 0.0, "thinking": 3.0, "answering": 6.0}[name]
        gen = int(at * SPEED) if name == "answering" else None
        return {"state": "generating", "queued": 0, "phase": name, "prompt_tokens": PROMPT_TOKENS,
                "prompt_read": int(PROMPT_TOKENS * at / dur) if name == "reading the prompt" else None,
                "prompt_total": PROMPT_TOKENS if name == "reading the prompt" else None, "generated": gen, "max_tokens": 4000,
                "elapsed_s": round(lead + at, 1), "tok_s": round(SPEED + 3 * ((at * 7) % 1 - 0.5), 1) if gen else None,
                "tok_s_mean": SPEED if gen else None, "parallel": slots, "running": 1}

    def metrics(self):
        a = self.a
        busy = self.phase()[0] != "idle"
        with self.lock:
            requests, totals = list(self.requests), dict(self.totals)
        primary = 10035
        return {"engine": {"model": a.model, "max_context": a.context, "version": "0.0.0", "engine": "0.0.0", "context": a.context, "kv": "int8",
                           "expert_cache_primary_mib": primary, "expert_cache_mib": primary + (6040 if a.helper else 0), "vram_free_mib": 2140,
                           "conversation_cache_slots": a.slots, "drafting": "mtp"},
                "live": self.live(), "requests": requests, "requests_kept": len(requests), "totals": totals,
                "hardware": {"gpu_util": 86 if busy else 4, "gpu_mem_used": 21.9 * 2**30, "gpu_mem_total": 24 * 2**30, "gpu_temp": 64 if busy else 41,
                             "gpu_power": 248.0 if busy else 36.0, "cpu": 21.0 if busy else 3.0, "ram_used": 19 * 2**30, "ram_total": 64 * 2**30},
                "hardware_static": {"gpu_name": "Demo Card A 24GB", "gpu_count": 2 if a.helper else 1, "cpu_name": "Demo 16-core processor",
                                    "cores": 16, "threads": 32},
                "time": time.time()}


def make_handler(world: World):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            world.tick()
            a = world.a
            if self.path == "/health":
                return self.send(200, {"status": "ok", "max_context": a.context, "model": a.model, "images": False,
                                       "api_key": bool(a.key), "loaded": True, "service": "strata"})
            if self.path == "/metrics":
                if a.key and self.headers.get("Authorization") != "Bearer " + a.key:
                    return self.send(401, {"error": "an API key is needed"})
                return self.send(200, world.metrics())
            if self.path == "/":
                data = b"<html><body><h1>Stand-in for Strata</h1><p>This is a test server. It loads no model.</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None
            return self.send(404, {"error": "not found"})
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", default="")
    ap.add_argument("--model", default="demo-model-q4")
    ap.add_argument("--context", type=int, default=32768)
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--key", default="")
    ap.add_argument("--seed-answers", type=int, default=0)
    ap.add_argument("--hold", default="", choices=("", "idle", "reading the prompt", "thinking", "answering"))
    ap.add_argument("--helper", action="store_true", help="pretend a second card holds part of the model")
    a = ap.parse_args(argv)
    world = World(a)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(world))
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
