from strata_launcher import APP_NAME, autostart


class FakeRegistry:
    """Stands in for the Run key under HKEY_CURRENT_USER."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_linux_off_by_default_then_on_and_off(tmp_path):
    a = autostart.get("linux", env={"XDG_CONFIG_HOME": str(tmp_path)})
    assert isinstance(a, autostart.LinuxAutostart)
    assert a.path == tmp_path / "autostart" / "strata-launcher.desktop"
    assert not a.is_enabled()
    a.enable(["/opt/strata-launcher/strata-launcher"])
    text = a.path.read_text(encoding="utf-8")
    assert a.is_enabled()
    assert "Exec=/opt/strata-launcher/strata-launcher --at-login\n" in text
    assert "Type=Application" in text and f"Name={APP_NAME}" in text and "Terminal=false" in text
    a.disable()
    assert not a.is_enabled() and not a.path.exists()
    a.disable()                                              # turning it off twice is fine


def test_linux_uses_home_when_xdg_is_not_set(tmp_path):
    a = autostart.LinuxAutostart(env={"HOME": str(tmp_path)})
    assert a.path == tmp_path / ".config" / "autostart" / "strata-launcher.desktop"


def test_linux_quotes_paths_with_spaces(tmp_path):
    a = autostart.LinuxAutostart(env={"XDG_CONFIG_HOME": str(tmp_path)})
    a.enable(["/home/some one/venv/bin/python", "-m", "strata_launcher"])
    assert 'Exec="/home/some one/venv/bin/python" -m strata_launcher --at-login\n' in a.path.read_text(encoding="utf-8")


def test_linux_respects_an_entry_the_desktop_switched_off(tmp_path):
    a = autostart.LinuxAutostart(env={"XDG_CONFIG_HOME": str(tmp_path)})
    a.enable(["strata-launcher"])
    a.path.write_text(a.path.read_text(encoding="utf-8") + "Hidden=true\n", encoding="utf-8")
    assert not a.is_enabled()


def test_windows_run_value():
    reg = FakeRegistry()
    a = autostart.get("win32", registry=reg)
    assert isinstance(a, autostart.WindowsAutostart) and not a.is_enabled()
    a.enable([r"C:\Users\someone\AppData\Local\Programs\Strata Launcher\StrataLauncher.exe"])
    assert reg.values == {APP_NAME: r'"C:\Users\someone\AppData\Local\Programs\Strata Launcher\StrataLauncher.exe" --at-login'}
    assert a.is_enabled()
    a.disable()
    assert reg.values == {} and not a.is_enabled()
    a.disable()
    assert "CurrentVersion\\Run" in a.where() and a.where().startswith("HKEY_CURRENT_USER")


def test_launcher_command_installed_and_from_source():
    assert autostart.launcher_command(frozen=True, executable="/opt/x/strata-launcher") == ["/opt/x/strata-launcher"]
    assert autostart.launcher_command(frozen=False, executable="/venv/bin/python") == ["/venv/bin/python", "-m", "strata_launcher"]


def test_linux_entry_names_the_installed_icon_file_when_there_is_one(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_DATA_HOME": str(tmp_path / "data")}
    a = autostart.LinuxAutostart(env=env)
    a.enable(["strata-launcher"])
    assert "Icon=strata-launcher\n" in a.path.read_text(encoding="utf-8")          # no icon file: the name
    icon = tmp_path / "data" / "icons" / "hicolor" / "256x256" / "apps" / "strata-launcher.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"png")
    a.enable(["strata-launcher"])
    assert f"Icon={icon}\n" in a.path.read_text(encoding="utf-8")
