# PyInstaller recipe for the packaged program, the same on Windows and Linux.
#   pyinstaller packaging/strata-launcher.spec --noconfirm --distpath out/dist --workpath out/build
# Result: out/dist/StrataLauncher/ (Windows) or out/dist/strata-launcher/ (Linux), a folder that runs without Python.
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PKG = os.path.join(ROOT, "src", "strata_launcher")
WINDOWS = sys.platform.startswith("win")
NAME = "StrataLauncher" if WINDOWS else "strata-launcher"
VERSION = re.search(r'__version__ = "([^"]+)"', open(os.path.join(PKG, "__init__.py"), encoding="utf-8").read()).group(1)

version_file = None
if WINDOWS:
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)
    numbers = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "Strata Launcher"), StringStruct("ProductName", "Strata Launcher"),
            StringStruct("FileVersion", VERSION), StringStruct("ProductVersion", VERSION),
            StringStruct("OriginalFilename", NAME + ".exe"), StringStruct("InternalName", NAME)])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])])])

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    datas=[(os.path.join(PKG, "default_options.json"), "strata_launcher"), (os.path.join(PKG, "assets"), os.path.join("strata_launcher", "assets"))],
    hiddenimports=[],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtOpenGL", "PySide6.QtDBus" if WINDOWS else "_none_"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=os.path.join(PKG, "assets", "icon.ico") if WINDOWS else None,
    version=version_file,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
