"""Finding the `strata` command and reading what it prints. Parsing only: the commands themselves run in proc.Runner."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from .paths import is_windows

# Where the command sits inside a folder the person points at, most likely first.
RELATIVE = ("bin/strata", "strata", "terminal/current/bin/strata", "current/bin/strata")
WINDOWS_ENDINGS = (".cmd", ".exe", ".bat")


def _candidates(folder: Path, platform: str | None) -> list[Path]:
    out = []
    for rel in RELATIVE:
        if is_windows(platform):
            out += [folder / (rel + ext) for ext in WINDOWS_ENDINGS]
        else:
            out.append(folder / rel)
    return out


def find_in_folder(folder, platform: str | None = None) -> str | None:
    """The strata command inside a folder (a Strata install folder, its bin folder, or the folder with the command)."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    for c in _candidates(folder, platform):
        if c.is_file():
            return str(c)
    return None


def accept(path, platform: str | None = None) -> str | None:
    """Takes what the person picked, a folder or the command itself, and returns the command or None."""
    if not path:
        return None
    p = Path(str(path).strip().strip('"')).expanduser()
    if p.is_dir():
        return find_in_folder(p, platform)
    if p.is_file() and p.name.lower().split(".")[0] == "strata":
        return str(p)
    return None


def usual_folders(env: dict | None = None, platform: str | None = None) -> list[Path]:
    """The folders Strata's own installers use, and the one STRATA_DATA_DIR names."""
    env = os.environ if env is None else env
    out = []
    if env.get("STRATA_DATA_DIR"):
        out.append(Path(env["STRATA_DATA_DIR"]))
    if is_windows(platform):
        if env.get("LOCALAPPDATA"):
            out.append(Path(env["LOCALAPPDATA"]) / "Strata")
    else:
        home = Path(env.get("HOME") or Path.home())
        out.append(home / ".local" / "bin")
        out.append(Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / "strata")
    return out


def discover(env: dict | None = None, platform: str | None = None, which=shutil.which) -> str | None:
    """Looks for the strata command: on the PATH first, then in the usual folders. None when it is not found."""
    found = which("strata")
    if found:
        return str(found)
    for folder in usual_folders(env, platform):
        cmd = find_in_folder(folder, platform)
        if cmd:
            return cmd
    return None


# ---------- reading what strata prints ----------
def parse_json(text: str):
    """The JSON object in a command's output, or None. Lines before it (warnings) are skipped."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            return None
    return None


def parse_version(text: str) -> str | None:
    """'strata 0.2.11 (build ...)' -> '0.2.11'."""
    m = re.search(r"\bstrata\s+v?(\d+(?:\.\d+)+)", text or "", re.I)
    return m.group(1) if m else None


def context_text(tokens) -> str:
    if not isinstance(tokens, int) or tokens <= 0:
        return ""
    return f"{tokens // 1024}K" if tokens % 1024 == 0 else f"{tokens:,}"


def parse_presets(text: str) -> list[dict]:
    """The saved presets, newest use first: name, a line that describes it, and the fields the window uses."""
    data = parse_json(text)
    out = []
    for p in (data or {}).get("presets", []) if isinstance(data, dict) else []:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            continue
        bits = [str(p.get("model") or p.get("model_path") or "").strip(), context_text(p.get("context")),
                {"local": "this computer only", "lan": "network"}.get(p.get("access"), p.get("access") or "")]
        if isinstance(p.get("port"), int):
            bits.append(f"port {p['port']}")
        out.append({"name": p["name"], "about": ", ".join(b for b in bits if b), "family": p.get("family") or "",
                    "model": p.get("model") or "", "context": p.get("context"), "access": p.get("access") or "",
                    "port": p.get("port"), "last_used": str(p.get("last_used") or "")})
    out.sort(key=lambda p: p["last_used"], reverse=True)
    return out


def parse_models(text: str) -> list[dict]:
    """The models that are set up on this computer: id (what --model takes), full name, and whether files are missing."""
    data = parse_json(text)
    out = []
    for m in (data or {}).get("models", []) if isinstance(data, dict) else []:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str):
            continue
        out.append({"id": m["id"], "name": str(m.get("name") or m["id"]),
                    "complete": m.get("component_status", "present") == "present" and not m.get("missing"),
                    "context": context_text(m.get("context_tokens"))})
    return out


def parse_status(text: str) -> list[dict]:
    """What is serving now. An empty list: nothing. Reads the JSON form, and the plain form as a fallback."""
    data = parse_json(text)
    if isinstance(data, dict):
        out = []
        for r in data.get("running", []) or []:
            if isinstance(r, dict):
                out.append({"name": str(r.get("name") or ""), "state": str(r.get("state") or ""), "port": r.get("port"),
                            "url": str(r.get("url") or ""), "host": str(r.get("host") or ""), "config": str(r.get("config") or "")})
        return out
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^(\S.*?): (\w+) at (https?://\S+)", line.strip())
        if m:
            port = re.search(r":(\d+)", m.group(3)[8:])
            out.append({"name": m.group(1), "state": m.group(2), "url": m.group(3), "host": "", "config": "",
                        "port": int(port.group(1)) if port else None})
    return out


def parse_plan(text: str) -> dict | None:
    """What a start would do: how much it would download and Strata's warnings. None when the output is not a plan."""
    data = parse_json(text)
    if not isinstance(data, dict):
        return None
    if data.get("ok") is False:
        return {"ok": False, "error": str(data.get("error") or data.get("message") or "Strata refused these choices."),
                "download_gb": 0.0, "warnings": [], "title": "", "text": ""}
    plan = data.get("plan")
    if not isinstance(plan, dict):
        return None
    dl = plan.get("download") if isinstance(plan.get("download"), dict) else {}
    model = plan.get("model") if isinstance(plan.get("model"), dict) else {}
    try:
        gb = float(dl.get("gb") or 0)
    except (TypeError, ValueError):
        gb = 0.0
    return {"ok": True, "error": "", "download_gb": gb, "text": str(dl.get("text") or ""),
            "engine_missing": bool(dl.get("engine_missing")), "title": str(model.get("title") or ""),
            "warnings": [str(w) for w in plan.get("warnings", []) if w], "flags": str(plan.get("flags") or "")}


UPDATE_PARTS = (("engine", "Engine"), ("control", "Control"))
UPDATE_REFUSED_MODEL_LOADED = 7      # exit code of `strata update --yes` while a model is loaded: nothing was changed
UPDATE_DID_NOT_START_AGAIN = 8       # the update worked, but the model it stopped did not start again


def _version_of(value) -> str:
    """Strata gives a version as text or as an object with a "version" field."""
    if isinstance(value, dict):
        value = value.get("version")
    return str(value) if value else ""


def parse_update_check(text: str) -> dict | None:
    """What `strata update --check --output json` found. None when the output is not that.

    Returns {"ok", "available", "parts": [{"name", "installed", "offered", "mb"}], "installed": {...}, "download_mb",
    "serving", "problem", "source"}. Only the engine and the Control part count: they are what `strata update` installs.
    """
    data = parse_json(text)
    if not isinstance(data, dict) or not any(isinstance(data.get(key), dict) for key, _name in UPDATE_PARTS):
        return None
    out = {"ok": data.get("ok") is not False, "available": False, "parts": [], "installed": {}, "download_mb": 0.0,
           "serving": len(data.get("serving") or []), "problem": "", "source": str((data.get("feed") or {}).get("source") or "")}
    if not out["ok"]:
        out["problem"] = str(data.get("error") or data.get("message") or "Strata could not read its update feed.")
    for key, name in UPDATE_PARTS:
        part = data.get(key)
        if not isinstance(part, dict):
            continue
        installed, offered = _version_of(part.get("installed")), _version_of(part.get("offered"))
        if installed:
            out["installed"][name] = installed
        if part.get("problem"):
            out["problem"] = out["problem"] or str(part["problem"])
        if part.get("status") == "available" and offered and offered != installed:
            try:
                mb = float(part.get("download_bytes") or 0) / 1e6
            except (TypeError, ValueError):
                mb = 0.0
            out["parts"].append({"name": name, "installed": installed, "offered": offered, "mb": mb})
            out["download_mb"] += mb
    out["available"] = bool(out["parts"])
    return out


def update_text(found: dict) -> str:
    """One line for the window: what is installed, and what a newer version would bring."""
    if found["available"]:
        bits = [f"{p['name']} {p['installed'] or 'none'} → {p['offered']}" for p in found["parts"]]
        size = f" (about {found['download_mb']:,.0f} MB to download)" if found["download_mb"] >= 1 else ""
        return "A newer Strata is available: " + ", ".join(bits) + size + "."
    have = ", ".join(f"{name.lower()} {v}" for name, v in found["installed"].items())
    return "Strata is up to date" + (f" ({have})." if have else ".")


def update_key(found: dict) -> str:
    """Names one offer, so the same one is announced only once."""
    return ";".join(f"{p['name']}={p['offered']}" for p in found["parts"])


def parse_key(text: str) -> str | None:
    """The API key in the output of --show-key, or None."""
    for word in (text or "").split():
        if word.startswith("sk-strata-"):
            return word.strip("\"',;")
    return None


def launch_files(status: list[dict], port: int | None = None) -> dict:
    """Where the running launch (the one on `port` when there are several) keeps its engine log and its model.

    Strata's status names the launch's run file; that file names the log. If it does not, the log sits beside it.
    Returns {"engine_log": path or None, "model_dir": folder or None}.
    """
    out = {"engine_log": None, "model_dir": None}
    pick = None
    for r in status:
        if port is not None and r.get("port") == port:
            pick = r
            break
    pick = pick or (status[0] if status else None)
    if not pick or not pick.get("config"):
        return out
    cfg = Path(pick["config"])
    try:
        with open(cfg, encoding="utf-8") as f:
            run = json.load(f)
        if isinstance(run.get("log"), str) and run["log"]:
            out["engine_log"] = run["log"]
        args = run.get("args") if isinstance(run.get("args"), list) else []
        for flag in ("--pack", "--native"):
            if flag in args[:-1]:
                out["model_dir"] = str(Path(str(args[args.index(flag) + 1])).parent)
                break
    except (OSError, ValueError, AttributeError):
        pass
    if not out["engine_log"] and cfg.suffix == ".json":
        out["engine_log"] = str(cfg.with_suffix(".engine.log"))
    return out


def platform_name() -> str:
    return "Windows" if sys.platform.startswith("win") else "Linux" if sys.platform.startswith("linux") else sys.platform
