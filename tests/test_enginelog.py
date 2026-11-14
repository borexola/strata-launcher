from strata_launcher.enginelog import Facts, Perf, Tail, read_run, summarise

START = "[strata] 2026-01-02 03:04:05 engine started: /engine/strata --serve --pack /models/p --max-context 65536 --batch 3"
ANSWER = ("strata serve: prompt 1500 tokens = 500 reused + 1000 read in 800 ms (1250.0 tok/s), 300 generated in 3000 ms "
          "(100.0 tok/s), drafts accepted 60 of 100, 1 checkpoints")
CHECK = ("strata serve: prompt 60 tokens = 55 reused + 5 read in 85 ms (58.8 tok/s), 1 generated in 10 ms (104.4 tok/s), "
         "drafts accepted 0 of 0, 1 checkpoints")
HIT = "strata serve: decode expert cache hit rate: 97.5% (390 hits / 400 lookups); 10 more read by the GPU over PCIe or from another GPU (2.5% of all 400 routed)"
FILEIO = "strata serve: file tier I/O this request: the OS read 2500.0 MB from storage (900 major faults) for 10000.0 MB of expert reads; page cache held"
SETUP = [
    "strata generate: PCIe probe: 24.0 GB/s host->device (best of 24.0 24.0) -> pcie_frac 0.55 (default 0.55)",
    "strata generate: expert cache 5200 slots, 9.80 GiB of VRAM; policy is",
    "strata generate: CUDA1: 3100 additional experts, 5.90 GiB; results return through pinned host rows",
    "strata generate: resident RAM mode: 6.40 GiB of experts in RAM (page-locked), 5200 in the GPU cache",
    "strata generate: --batch 3: the slot sessions take 4.80 GiB of VRAM on CUDA0 that the expert cache would otherwise hold",
    "strata: prefill plan inputs: 22.1 GiB experts, 9.8 GiB in the GPU caches, 6.4 GiB RAM copy",
    "strata generate: session is up (engine 0.1.99)",
    "strata serve: 2140 MiB of VRAM free with everything loaded",
]


def kinds(records):
    return [k for k, _t in records]


def test_engine_start_line():
    perf = Perf()
    recs = perf.feed(START)
    assert kinds(recs) == ["start", "detail"]
    assert "context 64K" in recs[0][1] and "3 request slots" in recs[0][1] and "03:04:05" in recs[0][1]
    assert perf.context == 65536 and perf.slots == 3 and perf.started == "2026-01-02 03:04:05"


def test_where_the_model_sits():
    perf = Perf()
    perf.feed(START)
    texts = [t for line in SETUP for k, t in perf.feed(line) if k == "key"]
    assert texts == ["1st card holds 9.80 GiB of the model (5,200 parts)", "2nd card holds 5.90 GiB of the model (3,100 parts)",
                     "RAM holds 6.40 GiB of the model, locked in place", "Loaded. 2,140 MiB of graphics memory is free on the 1st card"]
    assert (perf.gpu0_gib, perf.gpu1_gib, perf.ram_gib, perf.slots_gib, perf.total_gib) == (9.8, 5.9, 6.4, 4.8, 22.1)
    assert perf.engine == "0.1.99" and perf.pcie == 24.0 and perf.vram_free == 2140
    facts = Facts(perf)
    assert facts.gpu0_gib == 9.8 and facts.checks == 0 and Facts().context is None


def test_low_graphics_memory_is_a_warning():
    recs = Perf().feed("strata serve: 300 MiB of VRAM free with everything loaded")
    assert recs[0][0] == "warn" and "that is low" in recs[0][1]


def test_an_answer_and_its_follow_up_lines():
    perf = Perf()
    recs = perf.feed(ANSWER)
    assert recs[0] == ("answer", "✔ Answer: 300 tokens at 100.0 tok/s in 3 s  ·  prompt 1,500 tokens (500 reused), read at 1,250 tok/s  ·  drafts 60% accepted")
    assert kinds(perf.feed(HIT)) == ["detail"]
    warn = perf.feed(FILEIO)
    assert warn[0][0] == "warn" and "2.5 GB came from the drive" in warn[0][1] and "75%" in warn[0][1]
    a = perf.answers[0]
    assert (a["hit"], a["other"], a["disk_mb"], a["faults"]) == (97.5, 2.5, 2500.0, 900)
    s = perf.summary()
    assert s["count"] == 1 and s["tokens"] == 300 and s["avg"] == 100.0 and s["read"] == 1250.0 and s["drafts"] == 60.0
    assert s["disk_gb"] == 2.5 and s["history"] == [100.0]


def test_health_checks_are_not_answers():
    perf = Perf()
    assert kinds(perf.feed(CHECK)) == ["detail"]
    assert perf.answers == [] and perf.checks == 1 and perf.summary() is None


def test_warnings_errors_and_unknown_lines():
    perf = Perf()
    assert perf.feed("strata serve: WARNING: only 446 MiB of VRAM free at the start of this request")[0][0] == "warn"
    assert perf.feed("strata generate: CUDA error: out of memory")[0][0] == "error"
    assert perf.feed("strata verify: captured the window (upload no error, sync no error)")[0][0] == "detail"
    assert kinds(perf.feed("a line from a newer engine that nobody knows yet")) == ["detail"]
    tight = perf.feed("strata: prefill plan: chunk 8192: file set 30.0 GiB does not fit the page cache 12.5 GiB -> more")
    assert tight[0][0] == "warn" and "30.0 GiB" in tight[0][1] and "12.5 GiB" in tight[0][1]


def test_summarise_many():
    answers = [{"out": 100, "ms": 1000, "tps": 100.0, "read": 0, "read_tps": 0, "acc": 0, "of": 0},
               {"out": 300, "ms": 2000, "tps": 150.0, "read": 500, "read_tps": 900.0, "acc": 10, "of": 40, "hit": 99.0, "other": 1.0}]
    s = summarise(answers)
    assert s["count"] == 2 and s["tokens"] == 400 and round(s["avg"], 1) == 133.3 and s["best"] == 150.0
    assert s["read"] == 900.0 and s["drafts"] == 25.0 and s["hit"] == 99.0
    assert summarise([]) is None


def test_read_run_starts_at_the_last_engine_start(tmp_path):
    log = tmp_path / "x.engine.log"
    older = START.replace("03:04:05", "01:00:00")
    log.write_text("\n".join([older, ANSWER, ANSWER, START] + SETUP + [ANSWER, "an unfinished li"]), encoding="utf-8")
    perf = Perf()
    recs, pos = read_run(str(log), perf)
    assert kinds(recs)[0] == "start" and "03:04:05" in recs[0][1]
    assert len(perf.answers) == 1                              # the two answers of the earlier run are not counted
    assert pos == log.stat().st_size - len("an unfinished li")
    assert read_run(str(tmp_path / "missing.log"), Perf()) == ([], None)


def test_key_lines_never_reach_the_window(tmp_path):
    log = tmp_path / "x.engine.log"
    log.write_text(START + "\nthe key is sk-strata-secret123\n" + ANSWER + "\n", encoding="utf-8")
    recs, _pos = read_run(str(log), Perf())
    assert not any("sk-strata-" in t for _k, t in recs) and "answer" in kinds(recs)


def test_tail_follows_added_lines_and_a_replaced_file(tmp_path):
    log = tmp_path / "x.engine.log"
    log.write_text(START + "\n", encoding="utf-8")
    tail = Tail()
    assert tail.read() == ([], False)                          # nothing to follow yet
    tail.follow(str(log))
    recs, first = tail.read()
    assert first and kinds(recs) == ["start", "detail"]
    assert tail.read() == ([], False)
    with open(log, "a", encoding="utf-8") as f:
        f.write(ANSWER + "\nhalf a li")
    recs, first = tail.read()
    assert not first and kinds(recs) == ["answer", "detail"]
    with open(log, "a", encoding="utf-8") as f:
        f.write("ne\n")
    assert [t for _k, t in tail.read()[0]] == ["half a line"]
    log.write_text(START + "\n", encoding="utf-8")            # shorter than before: read from its beginning
    recs, _ = tail.read()
    assert kinds(recs) == ["start", "detail"] and tail.perf.answers == []
    tail.follow(str(tmp_path / "other.log"))                  # another log: the figures start again
    assert tail.pos is None and tail.read() == ([], False)
