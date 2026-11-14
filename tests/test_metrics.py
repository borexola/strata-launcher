from types import SimpleNamespace

from strata_launcher import metrics
from strata_launcher.enginelog import summarise

HEALTH = {"status": "ok", "model": "demo-model", "api_key": True}


def test_live_state_stopped_loading_idle():
    assert metrics.live_state({})["kind"] == "stopped"
    assert metrics.live_state({"engine_pid": 12})["kind"] == "loading"
    idle = metrics.live_state({"health": HEALTH, "metrics": {"live": {"state": "idle", "parallel": 4}}})
    assert idle["kind"] == "idle" and idle["model"] == "demo-model" and idle["api_key"] and idle["slots"] == 4 and idle["has_metrics"]
    no_key = metrics.live_state({"health": HEALTH, "metrics": None, "metrics_denied": True})
    assert no_key["kind"] == "idle" and no_key["denied"] and not no_key["has_metrics"]


def test_live_state_busy():
    reading = metrics.live_state({"health": HEALTH, "metrics": {"live": {"state": "generating", "phase": "reading the prompt",
                                  "prompt_read": 2000, "prompt_total": 6000, "running": 1, "parallel": 2}}})
    assert reading["kind"] == "busy" and reading["phase"] == "reading the prompt" and reading["generated"] == 0
    assert (reading["prompt_done"], reading["prompt_total"]) == (2000, 6000)
    writing = metrics.live_state({"health": HEALTH, "metrics": {"live": {"state": "generating", "phase": "answering", "generated": 512,
                                  "elapsed_s": 6.5, "tok_s": None, "tok_s_mean": 88.0, "queued": 2}}})
    assert writing["generated"] == 512 and writing["tok_s"] == 88.0 and writing["queued"] == 2 and writing["elapsed"] == 6.5


def test_answers_from_requests_skip_health_checks_and_sort():
    requests = [
        {"id": "b", "time": 20, "output_tokens": 200, "decode_ms": 2000.0, "decode_tok_s": None, "prompt_tokens": 900, "reused": 100,
         "prompt_read": 800, "prompt_ms": 400.0, "hit_rate": 0.98, "pcie_share": 0.02, "drafts_offered": 50, "drafts_accepted": 30, "file_mb": 0},
        {"id": "w", "time": 15, "output_tokens": 1, "decode_ms": 8.0},
        {"id": "a", "time": 10, "output_tokens": 100, "decode_ms": 1000.0, "decode_tok_s": 99.0, "prompt_tokens": 50, "prompt_read": 5, "prompt_ms": 80.0},
        "junk"]
    answers = metrics.answers_from_requests(requests)
    assert [a["id"] for a in answers] == ["a", "b"]
    b = answers[1]
    assert b["tps"] == 100.0 and b["read_tps"] == 2000.0 and b["hit"] == 98.0 and b["other"] == 2.0 and (b["acc"], b["of"]) == (30, 50)
    s = summarise(answers)
    assert s["count"] == 2 and s["tokens"] == 300 and s["read"] == 2000.0 and s["drafts"] == 60.0
    assert metrics.answers_from_requests(None) == []


def test_cards_fall_back_to_what_the_server_reports():
    driver = [{"index": 0, "name": "Card A", "used": 1000.0, "total": 8192.0}]
    assert metrics.cards({"gpus": driver}) == driver
    served = metrics.cards({"gpus": [], "metrics": {"hardware": {"gpu_mem_used": 4 * 2**30, "gpu_mem_total": 8 * 2**30, "gpu_util": 50, "gpu_temp": 60},
                                                    "hardware_static": {"gpu_name": "NVIDIA GeForce Example 8GB"}}})
    assert served[0]["name"] == "Example 8GB" and served[0]["used"] == 4096 and served[0]["total"] == 8192
    assert metrics.cards({}) == []


def test_memory_picture_adds_up():
    sample = {"health": HEALTH, "engine_pid": 1, "gpus": [{"index": 0, "name": "Card A", "used": 20 * 1024.0, "total": 24 * 1024.0},
                                                         {"index": 1, "name": "Card B", "used": 7 * 1024.0, "total": 16 * 1024.0}],
              "metrics": {"engine": {"expert_cache_primary_mib": 10 * 1024, "expert_cache_mib": 16 * 1024}},
              "ram_total": 64.0, "ram_avail": 40.0, "eng_locked": 6.0, "eng_work": 3.0, "eng_files": 2.0}
    bars = metrics.memory_picture(sample, SimpleNamespace(gpu0_gib=1.0, gpu1_gib=1.0, slots_gib=3.0))
    assert [b[0] for b in bars] == ["Card A", "Card B", "RAM"]
    for _name, total, segs in bars:
        assert abs(sum(amount for _l, amount, _k in segs) - total) < 0.01
    a, b, ram = (dict((k, amount) for _l, amount, k in segs) for _n, _t, segs in bars)
    assert a == {"model": 10.0, "slots": 3.0, "other": 7.0, "free": 4.0}
    assert b == {"model": 6.0, "other": 1.0, "free": 9.0}
    assert ram == {"model": 6.0, "slots": 3.0, "other": 15.0, "file": 2.0, "free": 38.0}


def test_memory_picture_uses_the_log_when_the_server_gives_no_figures():
    sample = {"engine_pid": 1, "gpus": [{"index": 0, "name": "Card A", "used": 8 * 1024.0, "total": 12 * 1024.0}]}
    bars = metrics.memory_picture(sample, SimpleNamespace(gpu0_gib=5.0, gpu1_gib=None, slots_gib=None))
    assert dict((k, amount) for _l, amount, k in bars[0][2]) == {"model": 5.0, "other": 3.0, "free": 4.0}
    stopped = metrics.memory_picture({"gpus": sample["gpus"]}, SimpleNamespace(gpu0_gib=5.0, gpu1_gib=None, slots_gib=None))
    assert "model" not in dict((k, amount) for _l, amount, k in stopped[0][2])       # nothing is loaded when it is stopped


def test_small_helpers():
    assert metrics.plural(1, "slot") == "1 slot" and metrics.plural(3, "slot") == "3 slots"
    assert metrics.short_gpu("NVIDIA GeForce Example 24GB") == "Example 24GB"
    assert metrics.ram_needed_note({"ram_total": 64.0, "ram_avail": 1.0}) and metrics.ram_needed_note({"ram_total": 64.0, "ram_avail": 30.0}) is None
