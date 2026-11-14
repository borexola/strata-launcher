"""Strata Launcher: a small desktop window to set up, start, stop and watch a Strata server.

An unofficial companion to Strata (https://github.com/Niko1221/Strata).
"""

__version__ = "0.1.0"

APP_NAME = "Strata Launcher"
APP_ID = "strata-launcher"

# Where this project lives on GitHub, as "owner/name". The update check and the links in the window use it.
# A copy whose owner part is the placeholder "OWNER" keeps the update check off.
GITHUB_REPO = "borexola/strata-launcher"

STRATA_URL = "https://github.com/Niko1221/Strata"


def repo_is_set(repo: str = "") -> bool:
    repo = repo or GITHUB_REPO
    owner, _, name = repo.partition("/")
    return bool(owner and name) and owner != "OWNER"
