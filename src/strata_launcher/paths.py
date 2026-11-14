"""Where the launcher keeps its own files. Nothing is ever written next to the program."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_ID, APP_NAME


def is_windows(platform: str | None = None) -> bool:
    return (platform or sys.platform).startswith("win")


def config_dir(env: dict | None = None, platform: str | None = None) -> Path:
    """The per-user settings folder.

    STRATA_LAUNCHER_CONFIG_DIR overrides it (used by the tests and by people who want a portable setup).
    Windows: %APPDATA%\\Strata Launcher.  Linux: $XDG_CONFIG_HOME/strata-launcher (normally ~/.config/strata-launcher).
    """
    env = os.environ if env is None else env
    if env.get("STRATA_LAUNCHER_CONFIG_DIR"):
        return Path(env["STRATA_LAUNCHER_CONFIG_DIR"])
    if is_windows(platform):
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
    return Path(base) / APP_ID


def state_dir(env: dict | None = None, platform: str | None = None) -> Path:
    """Where the launcher's own log goes. With STRATA_LAUNCHER_CONFIG_DIR set, it is a folder inside that one."""
    env = os.environ if env is None else env
    if env.get("STRATA_LAUNCHER_CONFIG_DIR"):
        return Path(env["STRATA_LAUNCHER_CONFIG_DIR"]) / "logs"
    if is_windows(platform):
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME / "logs"
    base = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local" / "state")
    return Path(base) / APP_ID


def config_file(env: dict | None = None, platform: str | None = None) -> Path:
    return config_dir(env, platform) / "config.json"


def user_options_file(env: dict | None = None, platform: str | None = None) -> Path:
    return config_dir(env, platform) / "options.json"


def log_file(env: dict | None = None, platform: str | None = None) -> Path:
    return state_dir(env, platform) / "launcher.log"


def asset(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def default_options_file() -> Path:
    return Path(__file__).resolve().parent / "default_options.json"
