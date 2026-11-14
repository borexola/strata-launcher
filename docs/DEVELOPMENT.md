# Working on Strata Launcher

```bash
git clone https://github.com/borexola/strata-launcher.git
cd strata-launcher
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m strata_launcher                          # run from source
ruff check . && pytest                             # lint and tests
```

Nothing in the tests needs Strata, a model or a graphics card. They use the stand-in in
[`tools/fake_strata`](../tools/fake_strata): a `strata` command that answers like Strata, a small server that pretends
to answer requests, and a stand-in `nvidia-smi`.

| Path | What it is |
| --- | --- |
| `src/strata_launcher/command.py` | Builds the `strata launch` command from the settings. |
| `src/strata_launcher/options.py`, `default_options.json` | The settings list and how your file is laid over it. |
| `src/strata_launcher/config.py` | Your saved choices, and bringing older files up to date. |
| `src/strata_launcher/strata.py` | Finding the `strata` command and reading what it prints. |
| `src/strata_launcher/enginelog.py`, `metrics.py` | Reading the engine log and the server's figures. |
| `src/strata_launcher/sampler.py`, `proc.py` | The once-a-second readings and every program the launcher starts. |
| `src/strata_launcher/autostart.py`, `updates.py` | Opening at sign-in, and the update check. |
| `src/strata_launcher/ui/` | The window (Qt, through PySide6). |
| `packaging/` | The PyInstaller recipe, the WiX installer and its test, the Linux download. |

**Try it without Strata:**

```bash
python tools/make_demo_config.py /tmp/demo-settings
STRATA_LAUNCHER_CONFIG_DIR=/tmp/demo-settings python -m strata_launcher
```

**The self-test** opens the real window, presses its real buttons (Save, Start three times, every tab, Stop twice, the
sign-in switch) and counts threads and programs before and after. It only runs against the stand-in:

```bash
STRATA_LAUNCHER_CONFIG_DIR=/tmp/demo-settings python -m strata_launcher --selftest
```

**Why the readings are built the way they are.** An earlier private version of this tool polled from a window timer.
The poll queued itself again and started hundreds of helper programs until the PC froze. So here one background thread
takes every reading on its own one-second clock, a reading starts no program, the graphics cards come from one
`nvidia-smi` that keeps running, the same kind of command never runs twice at once, and every program the launcher
starts is counted. The self-test fails if any of that stops being true.

**Build the downloads:**

```powershell
packaging\windows\build.ps1                                   # out\StrataLauncher-<version>-x64.msi (needs Python and the .NET SDK)
packaging\windows\test-installer.ps1 -Msi out\StrataLauncher-<version>-x64.msi
```

```bash
pip install . pyinstaller && packaging/linux/build.sh         # out/strata-launcher-<version>-linux-x86_64.tar.gz
```

**The README's pictures:** `QT_QPA_PLATFORM=offscreen python tools/make_screenshots.py docs/screenshots --theme dark --tabs settings,performance,log --max-height 1130`

**CI** (GitHub Actions): lint and tests on Linux and Windows, then the Windows installer is built with WiX, installed,
self-tested, upgraded and uninstalled, and the Linux download is built and self-tested.

**A release:** set the version in `src/strata_launcher/__init__.py`, commit, and push a tag `v<that version>`. The
release workflow tests, builds both downloads and publishes them with their checksums.
