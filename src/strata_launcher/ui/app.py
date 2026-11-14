"""Starting the window: one copy per person, the first-run question, and the options the tests use."""

from __future__ import annotations

import getpass
import hashlib
import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .. import APP_ID, APP_NAME, STRATA_URL, config, options, paths, strata
from ..proc import Runner
from . import theme
from .window import TABS, MainWindow


def make_file_log():
    """Returns a function that adds a line to the launcher's own log file. The file is kept under 2 MB."""
    path = paths.log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2 * 2**20:
            os.replace(path, path.with_name(path.name + ".old"))
    except OSError:
        pass

    def log(text: str):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass
    return log


def instance_name() -> str:
    """One name per person and settings folder, so two people (or a test) do not share a window."""
    who = f"{getpass.getuser()}|{paths.config_dir()}"
    return f"{APP_ID}-{hashlib.sha256(who.encode()).hexdigest()[:12]}"


def already_open(name: str) -> bool:
    """If the launcher is open already, ask that window to come to the front and say so."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def load_everything():
    cfg, notes, existed = config.load()
    opts, problems = options.load()
    first_run = not existed
    if not cfg.get("strata_command"):
        found = strata.discover()
        if found:
            cfg["strata_command"] = found
    if first_run:
        try:
            config.save(cfg)
        except OSError as e:
            notes.append(f"The settings could not be saved: {e}")
    return cfg, opts, notes + problems, first_run


def create(argv=None, single=True, **window_args):
    """Builds the application and the window. Returns (app, window), or (app, None) when a copy is already open."""
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv[:1])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    icon = paths.asset("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    server = None
    if single:
        name = instance_name()
        if already_open(name):
            return app, None
        QLocalServer.removeServer(name)
        server = QLocalServer(app)
        server.listen(name)
    cfg, opts, problems, first_run = load_everything()
    theme.apply(app, cfg.get("theme", "system"))
    log = make_file_log()
    win = MainWindow(cfg, opts, problems, first_run=first_run, runner=Runner(log), log=log, **window_args)
    if server is not None:
        def bring_up():
            conn = server.nextPendingConnection()
            if conn:
                conn.close()
            win.showNormal()
            win.raise_()
            win.activateWindow()
        server.newConnection.connect(bring_up)
    return app, win


def first_run_question(win: MainWindow):
    """Strata was not found: ask once where it is."""
    box = QMessageBox(win)
    box.setWindowTitle(APP_NAME)
    box.setIcon(QMessageBox.Question)
    box.setText("Where is Strata?")
    box.setInformativeText("The launcher did not find Strata on this computer. Choose the folder where Strata's Terminal is installed.\n\n"
                           f"If you do not have Strata yet, install it first: {STRATA_URL}")
    choose = box.addButton("Choose folder…", QMessageBox.AcceptRole)
    box.addButton("Later", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is choose:
        path = QFileDialog.getExistingDirectory(win, "Choose the folder where Strata is installed")
        if path:
            win.use_strata(strata.accept(path), path)


def run(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    at_login = "--at-login" in argv
    app, win = create(argv, at_login=at_login)
    if win is None:
        return 0
    if "--tab" in argv[:-1] and argv[argv.index("--tab") + 1] in TABS:
        win.tabs.setCurrentIndex(TABS.index(argv[argv.index("--tab") + 1]))
    if at_login and win.cfg.get("start_minimized"):
        win.showMinimized()
    else:
        win.show()
    if not win.cfg.get("strata_command") and not at_login:
        QTimer.singleShot(400, lambda: first_run_question(win))
    return app.exec()
