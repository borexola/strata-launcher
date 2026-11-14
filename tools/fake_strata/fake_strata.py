#!/usr/bin/env python3
"""A stand-in for the `strata` command, for tests and screenshots. It is NOT Strata: it loads no model, downloads
nothing and uses no graphics card. It answers the few commands the launcher runs, in the shape Strata prints them:

  strata --version
  strata models list --installed --output json
  strata launch --list-presets --output json
  strata launch --status [--output json]
  strata launch --show-key
  strata launch --stop
  strata launch <choices> --plan --output json
  strata launch <choices> --save-preset NAME --restart --yes --no-tui     (starts stub_server.py and returns)

Its files (what is "serving", saved presets, the engine log) live in the folder FAKE_STRATA_HOME names, or in a
folder called fake-strata in the system's temporary folder.

Environment switches for tests: FAKE_STRATA_LOAD_S (pretend loading time, default 1.5), FAKE_STRATA_FAIL=1 (the
start fails), FAKE_STRATA_HELPER=1 (pretend two cards), FAKE_STRATA_SEED (finished answers to begin with),
FAKE_STRATA_HOLD (keep the server in one phase).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "strata 0.0.0 (stand-in for tests; this is not Strata)"
KEY = "sk-strata-stand-in-0000000000000000"
MODELS = [{"id": "demo-q4", "name": "demo-model-q4", "context_tokens": 32768, "component_status": "present", "missing": []},
          {"id": "demo-q2", "name": "demo-model-q2", "context_tokens": 65536, "component_status": "present", "missing": []}]
SEED_PRESET = {"schema": "strata.launch/1", "name": "Demo 32K", "family": "demo", "model": "DEMO-Q4", "model_path": None,
               "context": 32768, "parallel": 2, "access": "local", "port": 8080, "last_used": "2026-01-01 10:00:00"}


def home() -> Path:
    p = Path(os.environ.get("FAKE_STRATA_HOME") or Path(tempfile.gettempdir()) / "fake-strata")
    (p / "launch").mkdir(parents=True, exist_ok=True)
    return p


def read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_.]+", "-", name.lower()).strip("-") or "launch"


def alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                                     # a finished child that nobody collected still has a pid
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def running() -> list[dict]:
    state = home() / "running.json"
    runs = [r for r in read(state, []) if alive(r["pid"])]
    write(state, runs)
    return runs


def kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    for _ in range(50):
        if not alive(pid):
            return
        time.sleep(0.1)


def flag(args: list[str], name: str, default=None):
    return args[args.index(name) + 1] if name in args[:-1] else default


def context_tokens(text) -> int:
    m = re.fullmatch(r"(\d+)\s*([kKmM]?)", str(text or ""))
    if not m:
        return 32768
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024 * 1024}[m.group(2).lower()]


def choices(args: list[str]) -> dict:
    """The launch choices on the command line, on top of a named preset when there is one."""
    presets = {p["name"]: p for p in read(home() / "presets.json", [SEED_PRESET])}
    base = dict(presets.get(flag(args, "--preset"), {}))
    c = {"model": base.get("model") or "", "family": base.get("family") or "", "model_path": base.get("model_path"),
         "context": base.get("context") or 32768, "parallel": base.get("parallel") or 2, "access": base.get("access") or "local",
         "port": base.get("port") or 8080}
    if flag(args, "--preset") and not base:
        raise SystemExit(f"No saved preset is called {flag(args, '--preset')!r}. See: strata launch --list-presets")
    for name, key in (("--model", "model"), ("--family", "family"), ("--model-path", "model_path"), ("--access", "access")):
        if flag(args, name) is not None:
            c[key] = flag(args, name)
    if flag(args, "--context") is not None:
        c["context"] = context_tokens(flag(args, "--context"))
    if flag(args, "--parallel") is not None:
        c["parallel"] = int(flag(args, "--parallel"))
    if flag(args, "--port") is not None:
        c["port"] = int(flag(args, "--port"))
    return c


def cmd_launch(args: list[str]) -> int:
    as_json = flag(args, "--output") == "json"
    if "--list-presets" in args:
        presets = read(home() / "presets.json", [SEED_PRESET])
        print(json.dumps({"schema_version": 1, "ok": True, "presets": presets}) if as_json else "\n".join(p["name"] for p in presets))
        return 0
    if "--status" in args:
        runs = [{"name": r["name"], "profile": "launch-" + r["name"], "state": "ready" if healthy(r["port"]) else "starting",
                 "port": r["port"], "url": f"http://127.0.0.1:{r['port']}", "host": r["host"], "config": r["config"]} for r in running()]
        if as_json:
            print(json.dumps({"schema_version": 1, "ok": True, "running": runs}))
        else:
            print("\n".join(f"{r['name']}: {r['state']} at {r['url']}" for r in runs) or "Nothing is serving.")
        return 0
    if "--show-key" in args:
        runs = running()
        if runs and runs[0].get("key"):
            print(f"API key: {runs[0]['key']}")
        else:
            print("This launch has no API key.")
        return 0
    if "--stop" in args:
        runs = running()
        if not runs:
            print("Nothing is serving.")
            return 0
        for r in runs:
            print(f"Stopping {r['name']} ...")
            kill(r["pid"])
        write(home() / "running.json", [])
        print("Stopped. The model is unloaded.")
        return 0

    c = choices(args)
    if not c["model"] and not c["model_path"]:
        print("Choose a model: add --model NAME, --model-path PATH or --preset NAME.")
        return 2
    missing = c["model"].lower() == "not-installed"
    if "--plan" in args:
        plan = {"settings": c, "warnings": ["This is a stand-in for Strata. No model is loaded."],
                "flags": "strata launch " + " ".join(a for a in args if a not in ("--plan", "--output", "json")),
                "model": {"title": f"Demo model {c['model'] or 'from your files'}", "family": c["family"], "model": c["model"], "model_gb": 12.3},
                "download": {"gb": 12.3 if missing else 0, "engine_missing": False,
                             "text": "12.3 GB to download: the model." if missing else "Nothing to download: the model is already on this computer."}}
        print(json.dumps({"schema_version": 1, "ok": True, "plan": plan}) if as_json else plan["download"]["text"])
        return 0

    port, name = c["port"], flag(args, "--save-preset") or flag(args, "--preset") or "launch"
    for r in running():
        if r["port"] == port:
            if "--restart" not in args:
                print(f"{r['name']} is already serving on port {port} with other settings. Add --restart or run: strata launch --stop")
                return 1
            print(f"Stopping {r['name']} first ...")
            kill(r["pid"])
    if os.environ.get("FAKE_STRATA_FAIL"):
        print("Starting the engine ...")
        print("error: the engine stopped while loading (this failure is pretended, for a test)")
        return 1

    key = KEY if c["access"] != "local" and "--no-api-key" not in args else ""
    run_name = slug(name)
    log = home() / "launch" / f"{run_name}.engine.log"
    config = home() / "launch" / f"{run_name}.json"
    model_name = "demo-model-" + (c["model"].lower().replace("demo-", "") if c["model"] else "own-files")
    write(config, {"exe": "stand-in", "args": ["--pack", str(home() / "models" / "pack"), "--max-context", str(c["context"])],
                   "model_name": model_name, "log": str(log), "port": port})
    server = [sys.executable, str(HERE / "stub_server.py"), "--port", str(port), "--log", str(log), "--model", model_name,
              "--context", str(c["context"]), "--slots", str(c["parallel"])]
    if key:
        server += ["--key", key]
    if os.environ.get("FAKE_STRATA_HELPER"):
        server.append("--helper")
    if os.environ.get("FAKE_STRATA_SEED"):
        server += ["--seed-answers", os.environ["FAKE_STRATA_SEED"]]
    if os.environ.get("FAKE_STRATA_HOLD"):
        server += ["--hold", os.environ["FAKE_STRATA_HOLD"]]
    print(f"Model: {model_name}, context {c['context'] // 1024}K, {c['parallel']} request slot(s)")
    print("[!] This is a stand-in for Strata. No model is loaded.")
    print("Starting the engine ...")
    sys.stdout.flush()
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        p = subprocess.Popen(server, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW, **quiet)
    else:
        p = subprocess.Popen(server, start_new_session=True, **quiet)
    time.sleep(float(os.environ.get("FAKE_STRATA_LOAD_S", "1.5")))
    for _ in range(100):
        if healthy(port):
            break
        if p.poll() is not None:
            print(f"error: the server could not start on port {port} (is the port in use?)")
            return 1
        time.sleep(0.1)
    else:
        print("error: the server did not answer in time")
        return 1
    runs = [r for r in running() if r["port"] != port]
    runs.append({"name": run_name, "pid": p.pid, "port": port, "host": "127.0.0.1" if c["access"] == "local" else "0.0.0.0",
                 "config": str(config), "key": key})
    write(home() / "running.json", runs)
    if flag(args, "--save-preset"):
        presets = [q for q in read(home() / "presets.json", [SEED_PRESET]) if q["name"] != name]
        presets.append({"schema": "strata.launch/1", "name": name, "family": c["family"], "model": c["model"], "model_path": c["model_path"],
                        "context": c["context"], "parallel": c["parallel"], "access": c["access"], "port": port,
                        "last_used": time.strftime("%Y-%m-%d %H:%M:%S")})
        write(home() / "presets.json", presets)
    print(f"Serving at http://127.0.0.1:{port}")
    if key:
        print(f"API key: {key}")
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "--version":
        print(VERSION)
        return 0
    if args[:2] == ["models", "list"]:
        print(json.dumps({"schema_version": 1, "ok": True, "scope": "local_run_configs", "models": MODELS, "model_count": len(MODELS)}))
        return 0
    if args[0] == "launch":
        return cmd_launch(args[1:])
    print(f"The stand-in does not know the command: {' '.join(args)}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
