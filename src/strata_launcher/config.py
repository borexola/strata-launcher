"""The person's saved choices: one JSON file in their settings folder, never in the program's folder."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import paths

VERSION = 2
SOURCE_KINDS = ("model", "files", "preset")

DEFAULTS = {
    "version": VERSION,
    # Where the `strata` command is. Empty until the first run finds it or the person picks it.
    "strata_command": "",
    # What to start: a model Strata knows, model files the person already has, or a preset saved in Strata.
    "source": {"kind": "model", "family": "", "model": "", "model_path": "", "preset": "", "data_dir": ""},
    # The launcher saves its choices in Strata under this preset name at every start.
    "preset_name": "Strata Launcher",
    # Extra `strata launch` flags added to every start, as the person would type them.
    "extra_flags": "",
    # The value of every setting in the settings list.
    "values": {},
    "start_minimized": False,
    "start_server_at_login": False,
    "own_scope": True,
    "check_updates": True,
    "last_update_check": 0,
    "skipped_version": "",
    "theme": "system",
    "nvidia_smi": "",
    # Remembered so the Log tab can show the last run before the server is started again.
    "engine_log": "",
    "log": {"details": False, "live": True, "follow": True},
    "window": {"width": 1000, "height": 860},
}


def fresh() -> dict:
    return copy.deepcopy(DEFAULTS)


def migrate(data) -> tuple[dict, list[str]]:
    """Brings a stored file of any earlier shape up to the current one. Returns (config, notes in plain words).

    Version 1 files had the model as flat fields and stored the address of the engine log; version 2 groups the
    model fields under "source" and finds the log by asking Strata. Unknown fields are dropped; fields of the
    wrong type go back to their default. A file that is not a JSON object gives a fresh config.
    """
    notes: list[str] = []
    if not isinstance(data, dict):
        return fresh(), ["The settings file was not readable as settings. Starting with fresh settings."]
    data = copy.deepcopy(data)
    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        version = 1
    if version > VERSION:
        notes.append("The settings file was written by a newer version of the launcher. Fields this version does not know are ignored.")
    if version < 2:
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        for old, new in (("family", "family"), ("model", "model"), ("model_path", "model_path"), ("preset", "preset"),
                         ("data_dir", "data_dir")):
            if isinstance(data.get(old), str) and data[old] and not source.get(new):
                source[new] = data[old]
        if "kind" not in source:
            source["kind"] = "preset" if source.get("preset") else "files" if source.get("model_path") else "model"
        data["source"] = source
        if "strata" in data and "strata_command" not in data:
            data["strata_command"] = data["strata"]
        if isinstance(data.get("base_flags"), list) and not data.get("extra_flags"):
            data["extra_flags"] = " ".join(_quote(str(x)) for x in data["base_flags"])
        notes.append("Your settings were brought up to this version's layout.")
    out = fresh()
    for key, default in DEFAULTS.items():
        if key == "version" or key not in data:
            continue
        v = data[key]
        if isinstance(default, dict):
            if not isinstance(v, dict):
                continue
            if key == "values":
                out[key] = v
                continue
            for k, d in default.items():
                if k in v and type(v[k]) is type(d):
                    out[key][k] = v[k]
        elif type(v) is type(default):
            out[key] = v
        elif isinstance(default, int) and not isinstance(default, bool) and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = int(v)
    if out["source"]["kind"] not in SOURCE_KINDS:
        out["source"]["kind"] = "model"
    if out["theme"] not in ("system", "light", "dark"):
        out["theme"] = "system"
    if not out["preset_name"].strip():
        out["preset_name"] = DEFAULTS["preset_name"]
    return out, notes


def _quote(text: str) -> str:
    return f'"{text}"' if (" " in text or not text) else text


def load(path: Path | None = None) -> tuple[dict, list[str], bool]:
    """Returns (config, notes, existed). A missing file is a first run; a damaged one is kept aside, not deleted."""
    path = paths.config_file() if path is None else path
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return fresh(), [], False
    except (OSError, ValueError) as e:
        notes = [f"The settings file could not be read ({e}). Starting with fresh settings."]
        try:
            aside = path.with_name(path.name + ".damaged")
            os.replace(path, aside)
            notes.append(f"The old file was kept as {aside.name}.")
        except OSError:
            pass
        return fresh(), notes, False
    cfg, notes = migrate(data)
    return cfg, notes, True


def save(cfg: dict, path: Path | None = None) -> None:
    """Writes the file in one step, so a crash never leaves half a file."""
    path = paths.config_file() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(cfg)
    out["version"] = VERSION
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)
