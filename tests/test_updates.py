import io

import pytest

from strata_launcher import repo_is_set, updates

RELEASE = {
    "tag_name": "v1.4.0", "html_url": "https://github.com/someone/strata-launcher/releases/tag/v1.4.0", "body": "What is new.",
    "draft": False, "prerelease": False,
    "assets": [
        {"name": "StrataLauncher-1.4.0-x64.msi", "browser_download_url": "https://github.com/someone/strata-launcher/releases/download/v1.4.0/StrataLauncher-1.4.0-x64.msi", "size": 50_000_000},
        {"name": "strata-launcher-1.4.0-linux-x86_64.tar.gz", "browser_download_url": "https://github.com/someone/strata-launcher/releases/download/v1.4.0/strata-launcher-1.4.0-linux-x86_64.tar.gz", "size": 60_000_000},
        {"name": "evil.msi", "browser_download_url": "ftp://elsewhere/evil.msi"},
    ],
}


@pytest.mark.parametrize("text,expected", [("v1.2.3", (1, 2, 3)), ("1.10", (1, 10)), ("2.0.0-beta1", (2, 0, 0)), ("nonsense", ()), ("", ())])
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize("latest,current,newer", [("v1.4.0", "1.3.9", True), ("1.10.0", "1.9.0", True), ("1.4", "1.4.0", False),
                                                  ("1.4.0", "1.4.0", False), ("1.3.0", "1.4.0", False), ("junk", "1.0", False), ("2.0", "", False)])
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


def test_the_right_file_for_each_system():
    assert updates.pick_asset(RELEASE, "win32")["name"] == "StrataLauncher-1.4.0-x64.msi"
    assert updates.pick_asset(RELEASE, "linux")["name"] == "strata-launcher-1.4.0-linux-x86_64.tar.gz"
    assert updates.pick_asset({"assets": [RELEASE["assets"][2]]}, "win32") is None          # only https links count
    assert updates.pick_asset({}, "linux") is None


def test_read_release():
    info = updates.read_release(RELEASE, "1.3.0", "win32")
    assert info["latest"] == "1.4.0" and info["newer"] and info["page"].endswith("/v1.4.0") and info["asset"]["size"] == 50_000_000
    assert not updates.read_release(RELEASE, "1.4.0", "linux")["newer"]
    assert updates.read_release(dict(RELEASE, draft=True)) is None
    assert updates.read_release(dict(RELEASE, prerelease=True)) is None
    assert updates.read_release({"message": "Not Found"}) is None and updates.read_release("text") is None


def test_where_to_ask():
    assert not repo_is_set("OWNER/strata-launcher") and repo_is_set("someone/strata-launcher") and not repo_is_set("nothing")
    assert updates.api_url("OWNER/strata-launcher", env={}) is None
    assert updates.api_url("someone/strata-launcher", env={}) == "https://api.github.com/repos/someone/strata-launcher/releases/latest"
    assert updates.api_url("OWNER/x", env={"STRATA_LAUNCHER_UPDATE_URL": "http://127.0.0.1:9/latest"}) == "http://127.0.0.1:9/latest"


def test_check_newer_same_and_failing():
    asked = []

    def fetcher(url):
        asked.append(url)
        return RELEASE
    newer = updates.check("1.0.0", "someone/strata-launcher", fetcher, "win32", env={})
    assert newer["ok"] and newer["newer"] and newer["latest"] == "1.4.0" and asked == ["https://api.github.com/repos/someone/strata-launcher/releases/latest"]
    same = updates.check("1.4.0", "someone/strata-launcher", fetcher, "linux", env={})
    assert same["ok"] and not same["newer"]

    def broken(url):
        raise OSError("no network")
    failed = updates.check("1.0.0", "someone/strata-launcher", broken, env={})
    assert not failed["ok"] and "OSError" in failed["why"]
    unset = updates.check("1.0.0", "OWNER/strata-launcher", fetcher, env={})
    assert not unset["ok"] and len(asked) == 2                                             # nothing was asked
    none = updates.check("1.0.0", "someone/strata-launcher", lambda url: {"message": "Not Found"}, env={})
    assert not none["ok"]


def test_due_once_a_day_and_only_when_switched_on():
    now = 1_000_000.0
    assert updates.due({"check_updates": True, "last_update_check": 0}, now)
    assert not updates.due({"check_updates": True, "last_update_check": now - 3600}, now)
    assert updates.due({"check_updates": True, "last_update_check": now - 25 * 3600}, now)
    assert not updates.due({"check_updates": False, "last_update_check": 0}, now)


def test_download_saves_under_the_file_name_only(tmp_path):
    seen = []
    asset = {"name": "..\\..\\StrataLauncher-1.4.0-x64.msi", "url": "https://example.invalid/x.msi", "size": 6}
    path = updates.download(asset, str(tmp_path), fetcher=lambda req: io.BytesIO(b"abcdef"), progress=lambda done, total: seen.append((done, total)))
    assert path == str(tmp_path / "StrataLauncher-1.4.0-x64.msi")
    assert (tmp_path / "StrataLauncher-1.4.0-x64.msi").read_bytes() == b"abcdef" and seen == [(6, 6)]
    assert not list(tmp_path.glob("*.part"))
