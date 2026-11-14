"""Opening the launcher when the person signs in. Off unless they turn it on. Per user: no administrator rights.

Linux: a .desktop file in the XDG autostart folder (normally ~/.config/autostart).
Windows: a value under HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, the place Windows'
own Task Manager lists under "Startup apps".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_ID, APP_NAME
from .paths import is_windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
FLAG = "--at-login"


def launcher_command(frozen: bool | None = None, executable: str | None = None) -> list[str]:
    """How to start this launcher again: the installed program, or Python with the package when run from source."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = executable or sys.executable
    if frozen:
        return [executable]
    if is_windows() and executable.lower().endswith("python.exe"):       # no console window at sign-in
        pythonw = executable[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(pythonw):
            executable = pythonw
    return [executable, "-m", "strata_launcher"]


def _desktop_quote(arg: str) -> str:
    """Quotes one argument for the Exec line of a .desktop file."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`%'):
        return arg
    for ch in ('\\', '"', '`', '$'):
        arg = arg.replace(ch, '\\' + ch)
    return '"' + arg.replace('%', '%%') + '"'


def _windows_quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


class LinuxAutostart:
    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self.env = env
        base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
        self.path = Path(base) / "autostart" / f"{APP_ID}.desktop"

    def is_enabled(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return False
        return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text

    def enable(self, cmd: list[str], icon: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        icon = icon or self.installed_icon() or APP_ID
        exec_line = " ".join(_desktop_quote(c) for c in cmd + [FLAG])
        self.path.write_text(
            "[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME}\nComment=Opens {APP_NAME} when you sign in\n"
            f"Exec={exec_line}\nIcon={icon}\nTerminal=false\nStartupNotify=false\nX-GNOME-Autostart-enabled=true\n",
            encoding="utf-8")

    def installed_icon(self) -> str:
        """The icon file install.sh put in place, if it is there. Naming the file works even before the desktop has
        noticed a newly installed icon."""
        base = self.env.get("XDG_DATA_HOME") or str(Path(self.env.get("HOME") or Path.home()) / ".local" / "share")
        path = Path(base) / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"
        return str(path) if path.is_file() else ""

    def disable(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def where(self) -> str:
        return str(self.path)


class WinRegistry:
    """The few registry calls the Windows switch needs, kept apart so the tests can put a stand-in here."""

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except OSError:
            pass


class WindowsAutostart:
    def __init__(self, registry=None):
        self.registry = registry or WinRegistry()

    def is_enabled(self) -> bool:
        return bool(self.registry.get(APP_NAME))

    def enable(self, cmd: list[str], icon: str = "") -> None:
        self.registry.set(APP_NAME, " ".join(_windows_quote(c) for c in cmd + [FLAG]))

    def disable(self) -> None:
        self.registry.delete(APP_NAME)

    def where(self) -> str:
        return "HKEY_CURRENT_USER\\" + RUN_KEY


def get(platform: str | None = None, env: dict | None = None, registry=None):
    return WindowsAutostart(registry) if is_windows(platform) else LinuxAutostart(env)
