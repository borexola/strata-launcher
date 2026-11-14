"""Turns the saved choices into `strata` command lines. Pure functions: nothing here starts a program."""

from __future__ import annotations

import os
import shlex


def split_flags(text: str, windows: bool | None = None) -> list[str]:
    """Splits a line of extra flags the way a terminal would. Quotes keep words together."""
    text = (text or "").strip()
    if not text:
        return []
    windows = (os.name == "nt") if windows is None else windows
    try:
        if not windows:
            return shlex.split(text)
        parts = shlex.split(text, posix=False)          # keeps backslashes in Windows paths
    except ValueError:
        return text.split()                             # an unclosed quote: fall back to plain words
    out = []
    for p in parts:
        if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
            p = p[1:-1]
        out.append(p)
    return out


def setting_flags(setting: dict, value) -> list[str]:
    """The flags one setting adds for one value. An empty value adds nothing."""
    if setting["type"] == "switch":
        return [str(x) for x in (setting.get("on", []) if value else setting.get("off", []))]
    if value is None or value == "":
        return []
    flags = setting.get("flags_for", {}).get(str(value), setting.get("flags", []))
    return [str(x).replace("{value}", str(value)) for x in flags]


def source_flags(source: dict) -> list[str]:
    """The flags that say what to run."""
    kind = source.get("kind", "model")
    out: list[str] = []
    if kind == "preset" and source.get("preset"):
        out += ["--preset", source["preset"]]
    elif kind == "files" and source.get("model_path"):
        out += ["--model-path", source["model_path"]]
    elif kind == "model":
        if source.get("family"):
            out += ["--family", source["family"]]
        if source.get("model"):
            out += ["--model", source["model"]]
    if source.get("data_dir") and kind != "preset":
        out += ["--data-dir", source["data_dir"]]
    return out


def source_problem(source: dict) -> str | None:
    """One plain sentence when there is nothing to start yet, else None."""
    kind = source.get("kind", "model")
    if kind == "preset" and not source.get("preset"):
        return "Choose a saved preset on the Setup tab first."
    if kind == "files" and not source.get("model_path"):
        return "Choose your model files on the Setup tab first."
    if kind == "model" and not source.get("model"):
        return "Choose a model on the Setup tab first."
    return None


def choice_flags(cfg: dict, opts: dict, windows: bool | None = None) -> list[str]:
    out = source_flags(cfg.get("source", {}))
    values = cfg.get("values", {})
    for s in opts["settings"]:
        out += setting_flags(s, values.get(s["key"], s.get("default")))
    return out + split_flags(cfg.get("extra_flags", ""), windows)


def launch_command(cfg: dict, opts: dict, windows: bool | None = None) -> list[str]:
    """What the Start button runs. It saves the choices as one Strata preset, replaces a running model, asks nothing
    and returns once the server answers."""
    return ([cfg["strata_command"], "launch"] + choice_flags(cfg, opts, windows)
            + ["--save-preset", cfg["preset_name"], "--restart", "--yes", "--no-tui"])


def plan_command(cfg: dict, opts: dict, windows: bool | None = None) -> list[str]:
    """Asks Strata what a start would do. Nothing is changed or downloaded."""
    return [cfg["strata_command"], "launch"] + choice_flags(cfg, opts, windows) + ["--plan", "--output", "json"]


def stop_command(cfg: dict) -> list[str]:
    return [cfg["strata_command"], "launch", "--stop"]


def status_command(cfg: dict) -> list[str]:
    return [cfg["strata_command"], "launch", "--status", "--output", "json"]


def update_check_command(cfg: dict) -> list[str]:
    """Asks Strata's own updater what it would change. It only reads Strata's signed update feed."""
    return [cfg["strata_command"], "update", "--check", "--output", "json"]


def update_command(cfg: dict, stop_model: bool = False) -> list[str]:
    """Runs Strata's own updater. It asks nothing. A loaded model is only stopped (and started again with the same
    settings afterwards) when stop_model is true; without it Strata refuses to update while a model is loaded."""
    return [cfg["strata_command"], "update", "--yes"] + (["--stop-model", "--start-again"] if stop_model else [])


def key_command(cfg: dict) -> list[str]:
    return [cfg["strata_command"], "launch", "--show-key"]


def presets_command(cfg: dict) -> list[str]:
    return [cfg["strata_command"], "launch", "--list-presets", "--output", "json"]


def models_command(cfg: dict) -> list[str]:
    return [cfg["strata_command"], "models", "list", "--installed", "--output", "json"]


def version_command(strata: str) -> list[str]:
    return [strata, "--version"]


def printable(cmd: list[str]) -> str:
    """A command as one line a person can read and copy."""
    out = []
    for c in cmd:
        c = str(c)
        out.append(f'"{c}"' if (not c or any(ch in c for ch in ' \t"&|<>^')) else c)
    return " ".join(out)
