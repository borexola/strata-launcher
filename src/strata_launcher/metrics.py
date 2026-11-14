"""Turns one reading (the server's /health and /metrics, the PC's memory) into what the window shows.

Pure functions, so they are tested without a window and without Strata.
"""

from __future__ import annotations

GIB = 2**30
MIB = 2**20


def live_state(sample: dict) -> dict:
    """What the server is doing right now.

    kind: "stopped" (no server, no engine), "loading" (engine in memory, no answer yet), "idle", "busy".
    """
    h = sample.get("health")
    m = sample.get("metrics") or {}
    live = m.get("live") or {}
    if not h:
        return {"kind": "loading" if sample.get("engine_pid") else "stopped"}
    busy = bool(live.get("running")) or live.get("state") not in (None, "idle")
    out = {"kind": "busy" if busy else "idle", "model": str(h.get("model") or "the model"), "api_key": bool(h.get("api_key")),
           "has_metrics": bool(m), "denied": bool(sample.get("metrics_denied")),
           "slots": live.get("parallel") or (m.get("engine") or {}).get("conversation_cache_slots")}
    if not busy:
        return out
    phase = str(live.get("phase") or live.get("state") or "working")
    out.update(phase=phase, running=live.get("running") or 1, queued=live.get("queued") or 0,
               generated=live.get("generated") or 0, elapsed=float(live.get("elapsed_s") or 0),
               tok_s=live.get("tok_s") or live.get("tok_s_mean"),
               prompt_done=live.get("prompt_read"), prompt_total=live.get("prompt_total") or live.get("prompt_tokens"))
    return out


def answers_from_requests(requests) -> list[dict]:
    """The finished answers in /metrics, oldest first, in the same shape the engine log reader makes.

    The server's own one-token health checks are left out.
    """
    out = []
    for r in requests or []:
        if not isinstance(r, dict):
            continue
        tokens = r.get("output_tokens") or 0
        if tokens <= 1:
            continue
        ms = float(r.get("decode_ms") or 0)
        tps = r.get("decode_tok_s") or (tokens * 1000.0 / ms if ms > 0 else 0.0)
        read = r.get("prompt_read") or 0
        prompt_ms = float(r.get("prompt_ms") or 0)
        hit = r.get("hit_rate")
        out.append({"prompt": r.get("prompt_tokens") or 0, "reused": r.get("reused") or 0, "read": read,
                    "read_tps": read * 1000.0 / prompt_ms if prompt_ms > 0 else 0.0, "out": int(tokens), "ms": int(ms),
                    "tps": float(tps), "acc": r.get("drafts_accepted") or 0, "of": r.get("drafts_offered") or 0,
                    "hit": 100.0 * hit if isinstance(hit, (int, float)) else None,
                    "other": 100.0 * r["pcie_share"] if isinstance(r.get("pcie_share"), (int, float)) else None,
                    "wait_ms": None, "disk_mb": r.get("file_mb"), "faults": None, "files_mb": None,
                    "time": r.get("time") or 0, "id": r.get("id")})
    out.sort(key=lambda a: a["time"])
    return out


def cards(sample: dict) -> list[dict]:
    """The graphics cards: from the driver's tool when it runs, else the one card the server reports about."""
    gpus = [g for g in sample.get("gpus") or [] if g.get("total")]
    if gpus:
        return gpus
    m = sample.get("metrics") or {}
    hw, static = m.get("hardware") or {}, m.get("hardware_static") or {}
    if hw.get("gpu_mem_total"):
        return [{"index": 0, "name": short_gpu(str(static.get("gpu_name") or "Graphics card")),
                 "util": hw.get("gpu_util"), "used": hw.get("gpu_mem_used", 0) / MIB, "total": hw["gpu_mem_total"] / MIB,
                 "temp": hw.get("gpu_temp"), "power": hw.get("gpu_power")}]
    return []


def short_gpu(name: str) -> str:
    for word in ("NVIDIA ", "GeForce ", "AMD ", "Intel(R) ", " Ada Generation", " Graphics"):
        name = name.replace(word, " " if word.startswith(" ") else "")
    return " ".join(name.split()) or "Graphics card"


def memory_picture(sample: dict, perf=None) -> list[tuple[str, float, list[tuple[str, float, str]]]]:
    """Where the graphics memory and the RAM go, as bars: [(name, total GiB, [(label, GiB, colour key)])].

    The cards come from the driver, the model's parts from the engine (its /metrics, else its log), the RAM from
    the system. Colour keys: model, slots, other, file, free.
    """
    eng = (sample.get("metrics") or {}).get("engine") or {}
    running = bool(sample.get("engine_pid")) or bool(sample.get("health"))
    primary, both = eng.get("expert_cache_primary_mib"), eng.get("expert_cache_mib")
    g0 = primary / 1024 if primary else (both / 1024 if both else (getattr(perf, "gpu0_gib", None) if running else None))
    g1 = (both - primary) / 1024 if both and primary else (getattr(perf, "gpu1_gib", None) if running else None)
    if g1 is not None and g1 < 0.05:
        g1 = None
    slots = getattr(perf, "slots_gib", None) if running else None
    bars = []
    for i, g in enumerate(cards(sample)):
        if g.get("used") is None:
            continue
        used, total = g["used"] / 1024, g["total"] / 1024
        model = min((g0 if i == 0 else g1 if i == 1 else None) or 0.0, used)
        sl = min((slots or 0.0) if i == 0 else 0.0, max(0.0, used - model))
        segs = [("the model", model, "model"), ("request slots", sl, "slots"),
                ("everything else", max(0.0, used - model - sl), "other"), ("free", max(0.0, total - used), "free")]
        bars.append((g["name"], total, [s for s in segs if s[1] > 0.005]))
    if sample.get("ram_total"):
        total, avail = sample["ram_total"], sample.get("ram_avail") or 0.0
        locked, work, files = sample.get("eng_locked") or 0.0, sample.get("eng_work") or 0.0, sample.get("eng_files") or 0.0
        others = max(0.0, (total - avail) - locked - work)
        files = min(files, max(0.0, avail))
        segs = [("the model, locked by the engine", locked, "model"), ("the engine's working memory", work, "slots"),
                ("other programs", others, "other"), ("model files kept in memory", files, "file"),
                ("free", max(0.0, avail - files), "free")]
        bars.append(("RAM", total, [s for s in segs if s[1] > 0.005]))
    return bars


def ram_needed_note(sample: dict) -> str | None:
    """One sentence when memory is nearly used up, else None."""
    total, avail = sample.get("ram_total"), sample.get("ram_avail")
    if not total or avail is None:
        return None
    if avail < max(1.0, 0.04 * total):
        return f"Only {avail:.1f} GiB of RAM is free. Closing other programs helps."
    return None


def plural(n, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
