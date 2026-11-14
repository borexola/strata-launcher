"""The list of settings the window shows. It is data: the built-in list plus the person's own options.json."""

from __future__ import annotations

import json
from pathlib import Path

from . import paths

TYPES = ("choice", "number", "switch", "text")
DEFAULT_ADDRESS = "http://127.0.0.1:{port}"


def _flag_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in value)


def check_setting(s) -> str | None:
    """Returns None for a usable setting, else one plain sentence that says what is wrong with it."""
    if not isinstance(s, dict):
        return "an entry in 'settings' is not an object"
    key = s.get("key")
    if not isinstance(key, str) or not key:
        return "a setting has no 'key'"
    kind = s.get("type")
    if kind not in TYPES:
        return f"setting '{key}': 'type' must be one of {', '.join(TYPES)}"
    if not isinstance(s.get("label"), str) or not s["label"]:
        return f"setting '{key}': 'label' is missing"
    if kind == "switch":
        if not _flag_list(s.get("on", [])) or not _flag_list(s.get("off", [])):
            return f"setting '{key}': 'on' and 'off' must be lists of flags"
        if not isinstance(s.get("default", False), bool):
            return f"setting '{key}': 'default' must be true or false"
        return None
    if not _flag_list(s.get("flags", [])):
        return f"setting '{key}': 'flags' must be a list"
    flags_for = s.get("flags_for", {})
    if not isinstance(flags_for, dict) or not all(_flag_list(v) for v in flags_for.values()):
        return f"setting '{key}': 'flags_for' must map values to lists of flags"
    if kind == "choice":
        opts = s.get("options")
        if not isinstance(opts, list) or not opts:
            return f"setting '{key}': 'options' must be a list with at least one choice"
        for o in opts:
            if not (isinstance(o, list) and len(o) == 2 and all(isinstance(x, str) for x in o)):
                return f"setting '{key}': every option must be [value, text shown]"
        if not s.get("editable") and s.get("default", opts[0][0]) not in [o[0] for o in opts]:
            return f"setting '{key}': 'default' is not one of its options"
    if kind == "number":
        for name in ("min", "max"):
            if not isinstance(s.get(name), int) or isinstance(s.get(name), bool):
                return f"setting '{key}': '{name}' must be a whole number"
        if s["min"] > s["max"]:
            return f"setting '{key}': 'min' is larger than 'max'"
        if not isinstance(s.get("step", 1), int) or s.get("step", 1) < 1:
            return f"setting '{key}': 'step' must be a whole number above 0"
        d = s.get("default")
        if d is None:
            if not s.get("optional"):
                return f"setting '{key}': 'default' is missing"
        elif not isinstance(d, int) or isinstance(d, bool) or not s["min"] <= d <= s["max"]:
            return f"setting '{key}': 'default' must be a whole number between 'min' and 'max'"
    if kind == "text" and not isinstance(s.get("default", ""), str):
        return f"setting '{key}': 'default' must be text"
    return None


def _normalise(s: dict) -> dict:
    s = dict(s)
    s.setdefault("help", "")
    s.setdefault("group", "Other")
    if s["type"] == "switch":
        s.setdefault("default", False)
        s.setdefault("on", [])
        s.setdefault("off", [])
    else:
        s.setdefault("flags", [])
        s.setdefault("flags_for", {})
        if s["type"] == "choice":
            s.setdefault("default", s["options"][0][0])
        elif s["type"] == "number":
            s.setdefault("step", 1)
            s.setdefault("default", None)
        else:
            s.setdefault("default", "")
    return s


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("the file must hold one JSON object")
    return data


def merge(base: dict, user: dict | None) -> tuple[dict, list[str]]:
    """Puts the person's file on top of the built-in one. Returns (options, problems in plain words).

    A broken entry is left out and named in the problems; it never stops the launcher from opening.
    """
    problems: list[str] = []
    settings: list[dict] = []
    for s in base.get("settings", []):
        why = check_setting(s)
        if why:
            problems.append(f"Built-in settings: {why}.")
        else:
            settings.append(_normalise(s))
    address = base.get("address") or DEFAULT_ADDRESS
    if user:
        mine = user.get("settings", [])
        if not isinstance(mine, list):
            problems.append("Your options.json: 'settings' must be a list. It was ignored.")
            mine = []
        for s in mine:
            why = check_setting(s)
            if why:
                problems.append(f"Your options.json: {why}. That entry was left out.")
                continue
            s = _normalise(s)
            for i, old in enumerate(settings):
                if old["key"] == s["key"]:
                    settings[i] = s
                    break
            else:
                settings.append(s)
        remove = user.get("remove", [])
        if isinstance(remove, list):
            settings = [s for s in settings if s["key"] not in remove]
        else:
            problems.append("Your options.json: 'remove' must be a list of keys. It was ignored.")
        if isinstance(user.get("address"), str) and user["address"].startswith("http"):
            address = user["address"]
    seen, unique = set(), []
    for s in settings:
        if s["key"] in seen:
            problems.append(f"The setting '{s['key']}' is listed twice. The first one is used.")
            continue
        seen.add(s["key"])
        unique.append(s)
    return {"address": address, "settings": unique}, problems


def load(user_file: Path | None = None, default_file: Path | None = None) -> tuple[dict, list[str]]:
    """Reads the built-in settings list and, if it exists, the person's own file."""
    base = _read(default_file or paths.default_options_file())
    user_file = paths.user_options_file() if user_file is None else user_file
    user, problems = None, []
    if user_file.exists():
        try:
            user = _read(user_file)
        except (OSError, ValueError) as e:
            problems.append(f"Your options.json could not be read ({e}). The built-in settings are used.")
    opts, more = merge(base, user)
    return opts, problems + more


def defaults(opts: dict) -> dict:
    return {s["key"]: s["default"] for s in opts["settings"]}


def valid(s: dict, v) -> bool:
    """Is v a value this setting can hold?"""
    kind = s["type"]
    if kind == "choice":
        if isinstance(v, str) and s.get("editable"):
            return True
        return v in [o[0] for o in s["options"]]
    if kind == "number":
        if v is None:
            return bool(s.get("optional"))
        return isinstance(v, int) and not isinstance(v, bool) and s["min"] <= v <= s["max"]
    if kind == "switch":
        return isinstance(v, bool)
    return isinstance(v, str)


def clean_values(opts: dict, stored: dict | None) -> dict:
    """The stored values where they still fit the settings list, and the defaults for the rest."""
    values = defaults(opts)
    for s in opts["settings"]:
        if isinstance(stored, dict) and s["key"] in stored and valid(s, stored[s["key"]]):
            values[s["key"]] = stored[s["key"]]
    return values


def address(opts: dict, values: dict) -> str:
    """The server's address for these values, without a slash at the end."""
    text = opts.get("address") or DEFAULT_ADDRESS
    for key, v in values.items():
        text = text.replace("{" + key + "}", str(v))
    if "{port}" in text:
        text = text.replace("{port}", "8080")
    return text.rstrip("/")


USER_FILE_TEMPLATE = {
    "about": [
        "Your own changes to the launcher's settings list. The launcher reads this file when it opens.",
        "'settings': an entry with the key of a built-in setting replaces it; a new key adds a setting.",
        "'remove': keys of built-in settings to hide.",
        "The built-in list, with every field explained, is default_options.json in the program's folder.",
        "'_example' below is ignored. To try it, copy it into the 'settings' list and change it.",
    ],
    "_example": {
        "key": "my_switch",
        "group": "Other",
        "label": "My own switch",
        "help": "What it does, in a sentence.",
        "type": "switch",
        "default": False,
        "on": ["--set", "some-engine-setting=on"],
        "off": [],
    },
    "settings": [],
    "remove": [],
}


def write_user_template(path: Path) -> None:
    """Creates the person's options.json with a short explanation, unless it is already there."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(USER_FILE_TEMPLATE, f, indent=2)
        f.write("\n")
