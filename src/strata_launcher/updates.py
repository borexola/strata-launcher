"""Learning about a newer version: one question to GitHub Releases, at most once a day.

Nothing about the person or their PC is sent: it is a plain web request for the project's latest release.
The check can be turned off on the App tab.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from . import GITHUB_REPO, __version__, repo_is_set
from .paths import is_windows

DAY = 24 * 3600


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Anything after the numbers ('-beta', '+build') is ignored. No numbers: ()."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def api_url(repo: str = "", env: dict | None = None) -> str | None:
    """Where to ask. STRATA_LAUNCHER_UPDATE_URL replaces it (the tests point it at a local file server)."""
    env = os.environ if env is None else env
    if env.get("STRATA_LAUNCHER_UPDATE_URL"):
        return env["STRATA_LAUNCHER_UPDATE_URL"]
    repo = repo or GITHUB_REPO
    if not repo_is_set(repo):
        return None
    return f"https://api.github.com/repos/{repo}/releases/latest"


def pick_asset(release: dict, platform: str | None = None) -> dict | None:
    """The file of a release that installs on this system: the .msi on Windows, the Linux archive on Linux."""
    want = (".msi",) if is_windows(platform) else (".tar.gz", ".tgz")
    for a in release.get("assets") or []:
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or "")
        if not url.lower().startswith(("https://", "http://127.0.0.1", "http://localhost")):
            continue
        if name.lower().endswith(want) and (is_windows(platform) or "linux" in name.lower()):
            return {"name": name, "url": url, "size": a.get("size") or 0}
    return None


def read_release(release, current: str = __version__, platform: str | None = None) -> dict | None:
    """What a GitHub "latest release" answer means for this copy. None: not a release, or a draft or test release."""
    if not isinstance(release, dict) or not release.get("tag_name") or release.get("draft") or release.get("prerelease"):
        return None
    latest = str(release["tag_name"])
    return {"latest": latest.lstrip("vV"), "newer": is_newer(latest, current), "page": str(release.get("html_url") or ""),
            "notes": str(release.get("body") or "").strip()[:2000], "asset": pick_asset(release, platform)}


def fetch(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "strata-launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def due(cfg: dict, now: float | None = None) -> bool:
    """Is it time for the automatic check? Only when it is switched on and the last one is a day old."""
    now = time.time() if now is None else now
    return bool(cfg.get("check_updates")) and now - float(cfg.get("last_update_check") or 0) >= DAY


def check(current: str = __version__, repo: str = "", fetcher=fetch, platform: str | None = None, env: dict | None = None) -> dict:
    """Asks once. Returns {"ok": False, "why": ...} or {"ok": True, "latest", "newer", "page", "notes", "asset"}."""
    url = api_url(repo, env)
    if not url:
        return {"ok": False, "why": "This copy does not know where its updates are published yet."}
    try:
        info = read_release(fetcher(url), current, platform)
    except Exception as e:
        return {"ok": False, "why": f"The update check did not get an answer ({type(e).__name__})."}
    if info is None:
        return {"ok": False, "why": "No published version was found."}
    info["ok"] = True
    return info


def download(asset: dict, folder: str, fetcher=None, progress=None) -> str:
    """Saves a release file into folder and returns its path. Only the file name of the asset is used, never a path."""
    name = os.path.basename(asset["name"].replace("\\", "/")) or "update"
    target = os.path.join(folder, name)
    req = urllib.request.Request(asset["url"], headers={"User-Agent": "strata-launcher"})
    opener = fetcher or (lambda r: urllib.request.urlopen(r, timeout=30))
    done = 0
    with opener(req) as r, open(target + ".part", "wb") as f:
        while True:
            block = r.read(256 * 1024)
            if not block:
                break
            f.write(block)
            done += len(block)
            if progress:
                progress(done, asset.get("size") or 0)
    os.replace(target + ".part", target)
    return target
