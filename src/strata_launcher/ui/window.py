"""The launcher's window: a status line on top, five tabs, and Save / Start / Stop at the bottom."""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, GITHUB_REPO, STRATA_URL, __version__, autostart, command, config, options, paths, repo_is_set, strata, updates
from ..metrics import cards, live_state, memory_picture, plural
from ..proc import Busy, Runner, own_scope, popen, redact
from ..sampler import GpuWatch, Sampler, http_json
from . import theme
from .widgets import Card, Dot, Meter, NoWheel, Row, SegmentBar, SpeedChart, Switch, Tile, label

TABS = ("settings", "performance", "log", "setup", "app")
LOG_KINDS = ("launcher", "answer", "live", "key", "warn", "error", "start", "detail")


class Bridge(QObject):
    """Carries results from the background threads to the window's thread. Qt queues each signal there."""

    ready = Signal()                 # the sampler has a new reading
    said = Signal(str, str)          # (kind, text): a line for the Log tab
    done = Signal(str, bool, str)    # (action, worked, message)
    info = Signal(dict)              # what Strata has installed
    ask = Signal(dict)               # a start would download something: ask first
    update = Signal(dict)            # the result of an update check
    key = Signal(str)                # the API key, for the clipboard
    fetched = Signal(str, str)       # (path of a downloaded update, error text)
    strata_update = Signal(object, bool)    # (what Strata's updater found or None, asked by hand)


def scroll(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(inner)
    return area


def page(*widgets, stretch=True) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(22, 16, 22, 18)
    lay.setSpacing(14)
    for x in widgets:
        lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w, lay


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, opts: dict, problems=(), first_run=False, runner: Runner | None = None, probe=None,
                 gpu: GpuWatch | None = None, at_login=False, log=None):
        super().__init__()
        self.cfg, self.opts = cfg, opts
        self.cfg["values"] = options.clean_values(opts, cfg.get("values"))
        self.file_log = log or (lambda text: None)
        self.runner = runner or Runner(self.file_log)
        self.bridge = Bridge()
        self.busy: str | None = None
        self.dirty = False
        self.loading = True                                  # true while the code itself fills the boxes
        self.sample: dict = {}
        self.records: list[tuple[str, str]] = []
        self.running = False
        self.was_ready = False
        self.stopped_at = 0.0
        self.live_logged = 0.0
        self.hot_said = 0.0
        self.shown_sub = None
        self.info: dict = {"version": None, "models": [], "presets": []}
        self.update_info: dict | None = None
        self.strata_found: dict | None = None                # what Strata's own updater last reported
        self.checking = False
        self.start_wanted = at_login and cfg.get("start_server_at_login")
        self.colours = theme.colours(cfg.get("theme", "system"))
        self.autostart = autostart.get()
        self.no_wheel = NoWheel(self)

        self.setWindowTitle(APP_NAME)
        icon = paths.asset("icon.png")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumSize(640, 520)
        width, height = int(cfg["window"].get("width", 1000)), int(cfg["window"].get("height", 860))
        screen = QGuiApplication.primaryScreen()
        if screen is not None:                               # never open larger than the screen (small laptops)
            room = screen.availableGeometry()
            width, height = min(width, room.width() - 40), min(height, room.height() - 60)
        self.resize(max(640, width), max(520, height))

        self._build()
        self._connect()
        self.apply_colours()
        self.loading = False
        self.show_status()
        self.refresh_preview()

        for p in problems:
            self.say(p, "warn")
        if first_run and cfg.get("strata_command"):
            self.say(f"Found Strata at {cfg['strata_command']}. Check the Setup tab, choose a model, then press Start.")
        elif not cfg.get("strata_command"):
            self.say("Strata was not found yet. Open the Setup tab and choose the folder where Strata is installed.", "warn")
            self.tabs.setCurrentIndex(TABS.index("setup"))

        self.sampler = Sampler(self.runner, self.bridge.ready.emit, probe=probe,
                               gpu=gpu if gpu is not None else GpuWatch(self.runner, cfg.get("nvidia_smi", "")), log=self.file_log)
        self.push_target()
        self.sampler.start()
        if cfg.get("strata_command"):
            self.load_info()
        if updates.due(cfg):
            QTimer.singleShot(4000, lambda: self.check_updates(False))

    # ------------------------------------------------------------------ building the window
    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # ----- status, shown above every tab -----
        self.dot = Dot()
        self.status_title = label("", "statusTitle")
        self.status_sub = label("", "dim", wrap=True, rich=True)
        self.status_sub.setOpenExternalLinks(True)
        self.status_sub.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.memory_line = label("", "caption", wrap=True)
        self.last_line = label("", "", wrap=True)
        self.last_line.setVisible(False)
        self.speed_big = label("", "big")
        self.speed_small = label("", "caption")
        for w in (self.speed_big, self.speed_small):
            w.setAlignment(Qt.AlignRight)
        self.btn_open = QPushButton("Open in browser")
        self.btn_open.setToolTip("Open the server's own page in your browser")
        self.btn_key = QPushButton("Copy API key")
        self.btn_key.setToolTip("Other devices and apps must send this key")
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self.dot, 0, Qt.AlignVCenter)
        head.addWidget(self.status_title, 1)
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addLayout(head)
        for w in (self.status_sub, self.memory_line, self.last_line):
            left.addWidget(w)
        right = QVBoxLayout()
        right.setSpacing(2)
        right.addWidget(self.speed_big)
        right.addWidget(self.speed_small)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn_key)
        buttons.addWidget(self.btn_open)
        right.addLayout(buttons)
        right.addStretch(1)
        top = QHBoxLayout()
        top.setContentsMargins(22, 14, 22, 10)
        top.setSpacing(16)
        top.addLayout(left, 1)
        top.addLayout(right)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.tabs.addTab(self._performance_tab(), "Performance")
        self.tabs.addTab(self._log_tab(), "Log")
        self.tabs.addTab(self._setup_tab(), "Setup")
        self.tabs.addTab(self._app_tab(), "App")
        root.addWidget(self.tabs, 1)

        self.btn_save = QPushButton("Save")
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("primary")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("danger")
        bar = QHBoxLayout()
        bar.setContentsMargins(22, 10, 22, 14)
        bar.setSpacing(12)
        for b in (self.btn_save, self.btn_start, self.btn_stop):
            b.setMinimumHeight(38)
            bar.addWidget(b, 1)
        root.addLayout(bar)

    # ----- Settings tab: built from the settings list -----
    def _settings_tab(self) -> QWidget:
        self.fields: dict[str, tuple[dict, QWidget]] = {}
        self.btn_defaults = QPushButton("Back to defaults")
        self.btn_defaults.setObjectName("flat")
        self.btn_defaults.setToolTip("Put every setting back to its default. Nothing is saved until you press Save.")
        cards_ = []
        groups: dict[str, Card] = {}
        for s in self.opts["settings"]:
            card = groups.get(s["group"])
            if card is None:
                card = groups[s["group"]] = Card(s["group"], "Changes take effect at the next Start." if not groups else "")
                if not cards_:
                    card.add_header_widget(self.btn_defaults)
                cards_.append(card)
            w = self._field(s)
            card.add_row(s["label"], s.get("help", ""), w)
            self.fields[s["key"]] = (s, w)
            self.set_field(s["key"], self.cfg["values"][s["key"]])
        if not cards_:
            cards_.append(label("The settings list is empty. See the Log tab for what is wrong with it.", "dim", wrap=True))
        inner, _ = page(*cards_)
        return scroll(inner)

    def _field(self, s: dict) -> QWidget:
        kind = s["type"]
        if kind == "choice":
            w = QComboBox()
            for value, text in s["options"]:
                w.addItem(text, value)
            if s.get("editable"):
                w.setEditable(True)
                w.setInsertPolicy(QComboBox.NoInsert)
                w.lineEdit().setPlaceholderText("or type your own")
                w.editTextChanged.connect(self.changed)
            w.currentIndexChanged.connect(self.changed)
        elif kind == "number":
            w = QSpinBox()
            step = s.get("step", 1)
            w.setRange(s["min"] - step if s.get("optional") else s["min"], s["max"])
            w.setSingleStep(step)
            if s.get("optional"):
                w.setSpecialValueText("Strata's default")
            w.setGroupSeparatorShown(False)
            w.valueChanged.connect(self.changed)
        elif kind == "switch":
            w = Switch()
            w.toggled.connect(self.changed)
        else:
            w = QLineEdit()
            w.setPlaceholderText(s.get("placeholder", ""))
            w.textChanged.connect(self.changed)
        if kind in ("choice", "number"):
            w.setFocusPolicy(Qt.StrongFocus)
            w.installEventFilter(self.no_wheel)
        return w

    def set_field(self, key: str, value):
        s, w = self.fields[key]
        if s["type"] == "choice":
            i = w.findData(value)
            if i >= 0:
                w.setCurrentIndex(i)
            elif s.get("editable"):
                w.setEditText(str(value))
        elif s["type"] == "number":
            w.setValue(w.minimum() if value is None else int(value))
        elif s["type"] == "switch":
            w.setChecked(bool(value))
        else:
            w.setText(str(value or ""))

    def get_field(self, key: str):
        s, w = self.fields[key]
        if s["type"] == "choice":
            if s.get("editable"):
                text = w.currentText().strip()
                i = w.findText(text)
                return w.itemData(i) if i >= 0 else text
            return w.currentData()
        if s["type"] == "number":
            return None if (s.get("optional") and w.value() == w.minimum()) else int(w.value())
        if s["type"] == "switch":
            return w.isChecked()
        return w.text().strip()

    # ----- Performance tab -----
    def _performance_tab(self) -> QWidget:
        def tiles(names, columns=4):
            grid, found = QGridLayout(), {}
            grid.setSpacing(12)
            for i, (key, title) in enumerate(names):
                found[key] = Tile(title)
                grid.addWidget(found[key], i // columns, i % columns)
            holder = QWidget()
            holder.setLayout(grid)
            grid.setContentsMargins(0, 0, 0, 0)
            return holder, found

        now_box, self.now_tiles = tiles((("state", "Right now"), ("speed", "Speed now"), ("disk", "Drive reads"), ("swap", "Swapping")))
        ans_box, self.tiles = tiles((("last", "Last answer"), ("avg", "Average"), ("best", "Fastest"), ("read", "Prompt reading"),
                                     ("count", "Answers"), ("tokens", "Tokens written"), ("drafts", "Drafts accepted"), ("hit", "Cache hits")))
        self.perf_note = label("", "help", wrap=True)
        self.chart = SpeedChart()
        self.chart_caption = label("", "caption")
        self.mem_note = label("", "help", wrap=True)
        self.mem_box = QVBoxLayout()
        self.mem_box.setSpacing(6)
        self.mem_bars: dict[str, tuple] = {}
        mem_holder = QWidget()
        mem_holder.setLayout(self.mem_box)
        self.mem_box.setContentsMargins(0, 0, 0, 0)

        self.run_card = Card("This run")
        self.run_rows = {}
        for key, title in (("engine", "Engine"), ("context", "Context and request slots"), ("model", "The model's parts"),
                           ("link", "Link speed to the 1st card"), ("disk", "Read from the drive since the start"),
                           ("watch", "Strata's own health checks")):
            value = label("-", selectable=True)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.run_card.add(Row(title, "", value, 0))
            self.run_rows[key] = value
        self.hw_card = Card("Hardware now", "Updated every second.")
        self.hw_rows: dict[str, Meter] = {}
        self.hw_order: dict[str, tuple] = {}

        inner, _ = page(label("Right now", "heading"), now_box, label("Answers in this run", "heading"), self.perf_note, ans_box,
                        self.chart, self.chart_caption, label("Where the memory goes", "heading"), self.mem_note, mem_holder,
                        self.run_card, self.hw_card)
        return scroll(inner)

    # ----- Log tab -----
    def _log_tab(self) -> QWidget:
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = self.log_view.font()
        font.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono", "monospace"])
        font.setStyleHint(font.StyleHint.Monospace)
        self.log_view.setFont(font)
        self.log_view.document().setDocumentMargin(12)
        self.legend = label("", "caption", rich=True)
        self.chk_live = QCheckBox("Live speed")
        self.chk_live.setToolTip("While the model writes, add a line with the current speed every 5 seconds")
        self.chk_details = QCheckBox("Engine details")
        self.chk_details.setToolTip("Also show every line the engine writes (grey)")
        self.chk_follow = QCheckBox("Auto-scroll")
        self.chk_follow.setToolTip("Keep the newest line in view")
        self.chk_live.setChecked(bool(self.cfg["log"].get("live", True)))
        self.chk_details.setChecked(bool(self.cfg["log"].get("details", False)))
        self.chk_follow.setChecked(bool(self.cfg["log"].get("follow", True)))
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip("Empty this view. The log files are kept.")
        tools = QHBoxLayout()
        tools.setContentsMargins(22, 8, 22, 8)
        tools.setSpacing(14)
        tools.addWidget(self.legend, 1)
        for w in (self.chk_live, self.chk_details, self.chk_follow, self.btn_clear):
            tools.addWidget(w)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(tools)
        lay.addWidget(self.log_view, 1)
        return w

    # ----- Setup tab: where Strata is and what to start -----
    def _setup_tab(self) -> QWidget:
        src = self.cfg["source"]
        # where Strata is
        card1 = Card("Strata", "The launcher needs the folder where Strata's Terminal is installed. It runs the strata command from there.")
        self.strata_edit = QLineEdit(self.cfg.get("strata_command", ""))
        self.strata_edit.setReadOnly(True)
        self.strata_edit.setPlaceholderText("Not chosen yet")
        self.btn_choose = QPushButton("Choose folder…")
        self.btn_find = QPushButton("Find it for me")
        self.strata_note = label("", "help", wrap=True, rich=True)
        self.strata_note.setOpenExternalLinks(True)
        row = QHBoxLayout()
        row.addWidget(self.strata_edit, 1)
        row.addWidget(self.btn_choose)
        row.addWidget(self.btn_find)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        card1.add(holder)
        card1.add(self.strata_note, divider=False)
        self.strata_update_label = label("Strata's own updater has not been asked yet.", "", wrap=True)
        self.btn_strata_check = QPushButton("Check for Strata updates")
        self.btn_strata_check.setToolTip("Asks Strata's own updater. It only reads Strata's signed update feed; nothing is changed.")
        self.btn_strata_update = QPushButton("Update Strata…")
        self.btn_strata_update.setObjectName("primary")
        self.btn_strata_update.setToolTip("Runs Strata's own updater (strata update). You are asked first.")
        self.btn_strata_update.setVisible(False)
        sr = QHBoxLayout()
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.strata_update_label, 1)
        sr.addWidget(self.btn_strata_update)
        sr.addWidget(self.btn_strata_check)
        sh = QWidget()
        sh.setLayout(sr)
        card1.add(sh)

        # what to start
        card2 = Card("What to start", "Pick one. The lists are read from your Strata.")
        self.btn_reload = QPushButton("Read again")
        self.btn_reload.setObjectName("flat")
        self.btn_reload.setToolTip("Ask Strata again which models and presets it has")
        card2.add_header_widget(self.btn_reload)
        self.kind_group = QButtonGroup(self)
        self.kind_buttons = {}
        kinds = QHBoxLayout()
        kinds.setSpacing(18)
        for i, (kind, text) in enumerate((("model", "A model Strata has set up"), ("files", "Model files I already have"), ("preset", "A preset saved in Strata"))):
            b = QRadioButton(text)
            self.kind_group.addButton(b, i)
            self.kind_buttons[kind] = b
            kinds.addWidget(b)
        kinds.addStretch(1)
        self.kind_buttons.get(src.get("kind", "model"), self.kind_buttons["model"]).setChecked(True)
        kh = QWidget()
        kh.setLayout(kinds)
        kinds.setContentsMargins(0, 0, 0, 0)
        card2.add(kh)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        self.model_combo.lineEdit().setPlaceholderText("for example q2_0")
        self.model_combo.setEditText(src.get("model", ""))
        self.family_edit = QLineEdit(src.get("family", ""))
        self.family_edit.setPlaceholderText("optional, for example qwen")
        self.files_edit = QLineEdit(src.get("model_path", ""))
        self.files_edit.setPlaceholderText("a folder of model files, or one .gguf file")
        self.btn_files_dir = QPushButton("Folder…")
        self.btn_files_file = QPushButton("File…")
        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(True)
        self.preset_combo.setInsertPolicy(QComboBox.NoInsert)
        self.preset_combo.setEditText(src.get("preset", ""))
        for w in (self.model_combo, self.preset_combo):
            w.setFocusPolicy(Qt.StrongFocus)
            w.installEventFilter(self.no_wheel)

        p_model = QWidget()
        lm = QVBoxLayout(p_model)
        lm.setContentsMargins(0, 0, 0, 0)
        lm.addWidget(Row("Model", "The size name Strata uses. Pick one that is set up, or type another one: Strata downloads it at the first start, after asking you.", self.model_combo, 300))
        lm.addWidget(Row("Family", "Only needed when several model families have a size with the same name.", self.family_edit, 300))
        p_files = QWidget()
        lf = QVBoxLayout(p_files)
        lf.setContentsMargins(0, 0, 0, 0)
        fr = QHBoxLayout()
        fr.addWidget(self.files_edit, 1)
        fr.addWidget(self.btn_files_dir)
        fr.addWidget(self.btn_files_file)
        lf.addWidget(label("Your model files", "rowTitle"))
        lf.addLayout(fr)
        p_preset = QWidget()
        lp = QVBoxLayout(p_preset)
        lp.setContentsMargins(0, 0, 0, 0)
        self.preset_row = Row("Preset", "The settings on the Settings tab change that preset for a start. Leave a setting on \"Strata's default\" to keep the preset's value.", self.preset_combo, 300)
        lp.addWidget(self.preset_row)
        self.kind_stack = QStackedWidget()
        for w in (p_model, p_files, p_preset):
            self.kind_stack.addWidget(w)
        self.kind_stack.setCurrentIndex(self.kind_group.checkedId())
        card2.add(self.kind_stack)

        self.data_edit = QLineEdit(src.get("data_dir", ""))
        self.data_edit.setPlaceholderText("Strata's own folder")
        self.btn_data = QPushButton("Folder…")
        dr = QHBoxLayout()
        dr.setContentsMargins(0, 0, 0, 0)
        dr.addWidget(self.data_edit, 1)
        dr.addWidget(self.btn_data)
        dh = QWidget()
        dh.setLayout(dr)
        self.data_row = Row("Where downloaded models go", "Leave empty to use the folder Strata already uses.", dh, 420)
        card2.add(self.data_row)

        # every start
        card3 = Card("Every start")
        self.name_edit = QLineEdit(self.cfg.get("preset_name", ""))
        self.extra_edit = QLineEdit(self.cfg.get("extra_flags", ""))
        self.extra_edit.setPlaceholderText("for example --set name=value")
        self.scope_switch = Switch()
        self.scope_switch.setChecked(bool(self.cfg.get("own_scope", True)))
        card3.add_row("Saved in Strata as", "The launcher keeps your choices in Strata as one preset with this name.", self.name_edit, 300)
        card3.add_row("Extra flags", "Any other strata launch flags, added to every start. Advanced: see strata launch --help.", self.extra_edit, 420)
        self.scope_row = card3.add_row("Keep the launcher open if Strata is stopped for lack of memory",
                                       "Linux only. Strata runs in a group of its own, so the system does not close the launcher with it.", self.scope_switch)
        self.scope_row.setVisible(sys.platform.startswith("linux"))

        # the command
        card4 = Card("The command", "This is what the Start button runs. You can copy it into a terminal.")
        self.preview = label("", "", wrap=True, selectable=True)
        font = self.preview.font()
        font.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono", "monospace"])
        self.preview.setFont(font)
        self.btn_copy = QPushButton("Copy")
        self.btn_plan = QPushButton("Check what a start would do")
        self.btn_plan.setToolTip("Asks Strata for its plan. Nothing is started, changed or downloaded.")
        br = QHBoxLayout()
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(self.btn_plan)
        br.addWidget(self.btn_copy)
        br.addStretch(1)
        bh = QWidget()
        bh.setLayout(br)
        card4.add(self.preview)
        card4.add(bh, divider=False)

        card5 = Card("Graphics cards found")
        self.cards_label = label("Looking…", "dim", wrap=True, selectable=True)
        card5.add(self.cards_label)

        inner, _ = page(card1, card2, card3, card4, card5)
        return scroll(inner)

    # ----- App tab: the launcher itself -----
    def _app_tab(self) -> QWidget:
        card1 = Card("When you sign in")
        self.sw_login = Switch()
        self.sw_minimized = Switch()
        self.sw_server = Switch()
        self.sw_login.setChecked(self.autostart.is_enabled())
        self.sw_minimized.setChecked(bool(self.cfg.get("start_minimized")))
        self.sw_server.setChecked(bool(self.cfg.get("start_server_at_login")))
        card1.add_row("Open the launcher when I sign in", "Off unless you turn it on. It only opens this window.", self.sw_login)
        self.row_minimized = card1.add_row("Open it minimized", "The window starts in the taskbar instead of on the screen.", self.sw_minimized)
        self.row_server = card1.add_row("Also start the server", "Loads the model as soon as the launcher opens at sign-in. This takes memory right away.", self.sw_server)

        card2 = Card("Updates")
        self.sw_updates = Switch()
        self.sw_updates.setChecked(bool(self.cfg.get("check_updates", True)))
        card2.add_row("Look for a new version once a day", "It asks GitHub for this project's latest release. Nothing about you or your PC is sent.", self.sw_updates)
        self.sw_strata_updates = Switch()
        self.sw_strata_updates.setChecked(bool(self.cfg.get("check_strata_updates", True)))
        card2.add_row("Look for a new Strata version once a day", "It runs Strata's own check (strata update --check), which reads Strata's signed update feed. "
                      "Updating itself is a button on the Setup tab and always asks first.", self.sw_strata_updates)
        self.update_label = label(f"You have version {__version__}.", "", wrap=True, rich=True)
        self.update_label.setOpenExternalLinks(True)
        self.btn_check = QPushButton("Check now")
        self.btn_get = QPushButton("Get it")
        self.btn_get.setObjectName("primary")
        self.btn_get.setVisible(False)
        ur = QHBoxLayout()
        ur.setContentsMargins(0, 0, 0, 0)
        ur.addWidget(self.update_label, 1)
        ur.addWidget(self.btn_get)
        ur.addWidget(self.btn_check)
        uh = QWidget()
        uh.setLayout(ur)
        card2.add(uh)

        card3 = Card("Look")
        self.theme_combo = QComboBox()
        for value, text in (("system", "Like my system"), ("light", "Light"), ("dark", "Dark")):
            self.theme_combo.addItem(text, value)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.cfg.get("theme", "system"))))
        card3.add_row("Colours", "", self.theme_combo, 200)

        card4 = Card("Your files", "Your settings are kept in your own settings folder, apart from the program.")
        self.btn_folder = QPushButton("Open my settings folder")
        self.btn_options = QPushButton("Change the settings list…")
        self.btn_options.setToolTip("Opens your options.json. Add, change or hide settings there, then open the launcher again.")
        self.btn_logfile = QPushButton("Open the launcher's log")
        fr = QHBoxLayout()
        fr.setContentsMargins(0, 0, 0, 0)
        for b in (self.btn_folder, self.btn_options, self.btn_logfile):
            fr.addWidget(b)
        fr.addStretch(1)
        fh = QWidget()
        fh.setLayout(fr)
        card4.add(fh)
        card4.add(label(html.escape(paths.short(paths.config_dir())), "help", wrap=True, selectable=True), divider=False)

        card5 = Card("About")
        project = f'<a href="https://github.com/{GITHUB_REPO}">github.com/{GITHUB_REPO}</a>' if repo_is_set() else ""
        about = label(f"<b>{APP_NAME} {__version__}</b><br>"
                      f"An unofficial companion to <a href=\"{STRATA_URL}\">Strata</a>. It is not made or endorsed by the Strata project. "
                      "It does not contain Strata: it runs the Strata you installed.<br>"
                      + (f"Project page: {project}" if project else ""), "", wrap=True, rich=True)
        about.setOpenExternalLinks(True)
        card5.add(about)
        inner, _ = page(card1, card2, card3, card4, card5)
        return scroll(inner)

    # ------------------------------------------------------------------ wiring
    def _connect(self):
        b = self.bridge
        b.ready.connect(self.on_ready)
        b.said.connect(self.on_said)
        b.done.connect(self.on_done)
        b.info.connect(self.on_info)
        b.ask.connect(self.on_ask)
        b.update.connect(self.on_update)
        b.key.connect(self.on_key_ready)
        b.fetched.connect(self.on_fetched)
        b.strata_update.connect(self.on_strata_checked)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_defaults.clicked.connect(self.on_defaults)
        self.btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.url() + "/")))
        self.btn_key.clicked.connect(self.on_key)
        self.btn_clear.clicked.connect(self.log_clear)
        self.chk_details.toggled.connect(self.log_redraw)
        self.chk_live.toggled.connect(self.log_redraw)
        self.chk_follow.toggled.connect(lambda *_: self.log_scroll())
        self.tabs.currentChanged.connect(lambda *_: QTimer.singleShot(0, self.log_scroll))
        # setup
        self.btn_choose.clicked.connect(self.on_choose)
        self.btn_find.clicked.connect(self.on_find)
        self.btn_reload.clicked.connect(self.load_info)
        self.btn_strata_check.clicked.connect(lambda: self.check_strata_update(True))
        self.btn_strata_update.clicked.connect(self.on_update_strata)
        self.sw_strata_updates.toggled.connect(lambda v: self.set_pref("check_strata_updates", v))
        self.kind_group.idClicked.connect(self.on_kind)
        for w in (self.family_edit, self.files_edit, self.data_edit, self.name_edit, self.extra_edit):
            w.textChanged.connect(self.changed)
        for w in (self.model_combo, self.preset_combo):
            w.editTextChanged.connect(self.changed)
        self.scope_switch.toggled.connect(self.changed)
        self.btn_files_dir.clicked.connect(lambda: self.pick_into(self.files_edit, True))
        self.btn_files_file.clicked.connect(lambda: self.pick_into(self.files_edit, False))
        self.btn_data.clicked.connect(lambda: self.pick_into(self.data_edit, True))
        self.btn_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.preview.text()))
        self.btn_plan.clicked.connect(self.on_plan)
        # app
        self.sw_login.toggled.connect(self.on_login_switch)
        self.sw_minimized.toggled.connect(lambda v: self.set_pref("start_minimized", v))
        self.sw_server.toggled.connect(lambda v: self.set_pref("start_server_at_login", v))
        self.sw_updates.toggled.connect(lambda v: self.set_pref("check_updates", v))
        self.theme_combo.currentIndexChanged.connect(self.on_theme)
        self.btn_check.clicked.connect(lambda: self.check_updates(True))
        self.btn_get.clicked.connect(self.on_get_update)
        self.btn_folder.clicked.connect(self.on_open_folder)
        self.btn_options.clicked.connect(self.on_open_options)
        self.btn_logfile.clicked.connect(self.on_open_log)
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.on_theme())
        except AttributeError:
            pass
        self.on_kind()
        self.refresh_login_rows()

    # ------------------------------------------------------------------ small helpers
    def url(self) -> str:
        return options.address(self.opts, self.cfg["values"])

    def push_target(self):
        src = self.cfg["source"]
        model_dir = src.get("data_dir") or (src.get("model_path") if src.get("kind") == "files" else "") or None
        self.sampler.set_target(self.url(), dict(self.cfg), self.cfg.get("engine_log") or None, model_dir)

    def say(self, line: str, kind: str = "launcher"):
        """A message from the launcher: in the Log tab with the time, in its own log file, and under the status."""
        line = redact(line)
        self.file_log(f"[{kind}] {line}")
        self.log_add([(kind, f"{time.strftime('%H:%M:%S')}  {line}")])
        self.last_line.setText(line)
        self.last_line.setVisible(True)

    def say_from_thread(self, line: str, kind: str = "launcher"):
        self.bridge.said.emit(kind, line)

    def on_said(self, kind: str, line: str):
        self.say(line, kind)

    def set_pref(self, key: str, value):
        """A switch on the App tab: it takes effect and is saved at once."""
        if self.loading:
            return
        self.cfg[key] = value
        self.save_cfg()

    def save_cfg(self):
        try:
            config.save(self.cfg)
        except OSError as e:
            self.say(f"The settings could not be saved: {e}", "error")

    # ------------------------------------------------------------------ colours
    def apply_colours(self):
        c = self.colours = theme.apply(QApplication.instance(), self.cfg.get("theme", "system"))
        for w in self.findChildren(Switch):
            w.colours = c
            w.update()
        self.legend.setText("&nbsp;&nbsp;&nbsp;".join(f'<span style="color:{c[k]}">■</span> {n}' for k, n in (
            ("launcher", "launcher"), ("answer", "answers"), ("live", "live speed"), ("key", "facts"), ("warn", "warnings"), ("error", "errors"))))
        self.formats = {}
        for k in LOG_KINDS:
            f = QTextCharFormat()
            f.setForeground(QColor(c[k]))
            if k in ("launcher", "answer", "start", "error"):
                f.setFontWeight(700)
            self.formats[k] = f
        self.log_redraw()
        self.chart.colours = c
        self.chart.update()
        for _t, bar, _l in self.mem_bars.values():
            bar.colours = c
            bar.update()
        self.shown_sub = None
        self.show_status()

    def on_theme(self, *_):
        if self.loading:
            return
        self.cfg["theme"] = self.theme_combo.currentData()
        self.save_cfg()
        self.apply_colours()
        if self.sample:
            self.refresh_live(self.sample)

    # ------------------------------------------------------------------ the Log tab
    def shown(self, kind: str) -> bool:
        return (kind != "detail" or self.chk_details.isChecked()) and (kind != "live" or self.chk_live.isChecked())

    def _append(self, items):
        cursor = QTextCursor(self.log_view.document())
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        for kind, text in items:
            if self.shown(kind):
                if not self.log_view.document().isEmpty():
                    cursor.insertBlock()
                cursor.insertText(text, self.formats.get(kind, self.formats["detail"]))
        cursor.endEditBlock()

    def log_add(self, new):
        """Adds lines (window thread only), keeps the last 6000, and scrolls down when Auto-scroll is on."""
        if not new:
            return
        self.records.extend(new)
        if len(self.records) > 6000:
            del self.records[:len(self.records) - 6000]
        self._append(new)
        self.log_scroll()

    def log_redraw(self, *_):
        self.log_view.clear()
        self._append(self.records)
        self.log_scroll()
        if not self.loading:
            self.cfg["log"] = {"details": self.chk_details.isChecked(), "live": self.chk_live.isChecked(), "follow": self.chk_follow.isChecked()}

    def log_clear(self):
        self.records.clear()
        self.log_view.clear()

    def log_scroll(self):
        if self.chk_follow.isChecked():
            bar = self.log_view.verticalScrollBar()
            bar.setValue(bar.maximum())

    # ------------------------------------------------------------------ Setup tab actions
    def on_kind(self, *_):
        i = max(0, self.kind_group.checkedId())
        self.kind_stack.setCurrentIndex(i)
        self.data_row.setVisible(i != 2)
        self.changed()

    def pick_into(self, edit: QLineEdit, folder: bool):
        start = edit.text() or str(Path.home())
        if folder:
            path = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a model file", start, "Model files (*.gguf);;All files (*)")
        if path:
            edit.setText(os.path.normpath(path))

    def on_choose(self):
        start = os.path.dirname(self.strata_edit.text()) or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose the folder where Strata is installed", start)
        if path:
            self.use_strata(strata.accept(path), path)

    def on_find(self):
        self.use_strata(strata.discover(), "")

    def use_strata(self, cmd: str | None, asked: str):
        if not cmd:
            where = f"in {asked}" if asked else "on this PC"
            self.say(f"No strata command was found {where}. Choose the folder that holds Strata's Terminal (it has a bin folder with strata in it).", "warn")
            self.refresh_strata_note()
            return
        self.strata_edit.setText(cmd)
        self.cfg["strata_command"] = cmd
        self.strata_found = None                             # another Strata: ask its updater afresh
        self.btn_strata_update.setVisible(False)
        self.strata_update_label.setText("Strata's own updater has not been asked yet.")
        self.save_cfg()
        self.push_target()
        self.say(f"Using Strata at {cmd}")
        self.refresh_preview()
        self.load_info()

    def refresh_strata_note(self):
        if not self.cfg.get("strata_command"):
            self.strata_note.setText("Strata is not chosen yet. If you do not have it, install Strata's Terminal first: "
                                     f'<a href="{STRATA_URL}/blob/main/docs/INSTALL.md#the-terminal">how to install Strata</a>.')
        elif self.info.get("version"):
            self.strata_note.setText(f"Strata {html.escape(self.info['version'])} answers here. "
                                     f"{plural(len(self.info['models']), 'model')} set up, {plural(len(self.info['presets']), 'preset')} saved.")
        elif self.info.get("failed"):
            self.strata_note.setText("This command did not answer like Strata. Choose the folder again.")
        else:
            self.strata_note.setText("Asking Strata what it has…")

    def load_info(self):
        """Asks Strata for its version, its models and its presets: three short commands, in a background thread."""
        cfg = dict(self.cfg)
        if not cfg.get("strata_command"):
            self.refresh_strata_note()
            return

        def work():
            try:
                code, out = self.runner.capture(command.version_command(cfg["strata_command"]), timeout=30, job="info")
            except Busy:
                return
            info = {"version": strata.parse_version(out), "models": [], "presets": [], "failed": code != 0}
            if info["version"]:
                _c, out = self.runner.capture(command.models_command(cfg), timeout=60)
                info["models"] = strata.parse_models(out)
                _c, out = self.runner.capture(command.presets_command(cfg), timeout=60)
                info["presets"] = strata.parse_presets(out)
            else:
                info["failed"] = True
            self.bridge.info.emit(info)
        self.info = {"version": None, "models": [], "presets": []}
        self.refresh_strata_note()
        threading.Thread(target=work, daemon=True, name="info").start()

    def on_info(self, info: dict):
        self.info = info
        self.loading = True
        model, preset = self.model_combo.currentText(), self.preset_combo.currentText()
        self.model_combo.clear()
        for m in info["models"]:
            self.model_combo.addItem(m["id"])
            self.model_combo.setItemData(self.model_combo.count() - 1, m["name"] + ("" if m["complete"] else " (files are missing)"), Qt.ToolTipRole)
        self.model_combo.setEditText(model)
        self.preset_combo.clear()
        for p in info["presets"]:
            self.preset_combo.addItem(p["name"])
            self.preset_combo.setItemData(self.preset_combo.count() - 1, p["about"], Qt.ToolTipRole)
        self.preset_combo.setEditText(preset)
        self.loading = False
        self.refresh_strata_note()
        if info.get("version") and self.strata_found is None and self.cfg.get("check_strata_updates", True) \
                and time.time() - float(self.cfg.get("last_strata_check") or 0) >= updates.DAY:
            self.check_strata_update(False)
        if info["models"] and not model and not preset and not self.files_edit.text() and self.kind_group.checkedId() == 0:
            self.model_combo.setEditText(info["models"][0]["id"])      # a first setup: start from a model that is there
            self.say(f"Strata has the model {info['models'][0]['id']} set up, so it is chosen. You can change it on the Setup tab.")
        if not info.get("version"):
            self.say("The chosen strata command did not answer like Strata. Check the Setup tab.", "warn")

    def collect(self) -> dict:
        """The config as the boxes show it now (not saved yet)."""
        cfg = dict(self.cfg)
        cfg["values"] = {key: self.get_field(key) for key in self.fields}
        kind = ("model", "files", "preset")[max(0, self.kind_group.checkedId())]
        cfg["source"] = {"kind": kind, "family": self.family_edit.text().strip(), "model": self.model_combo.currentText().strip(),
                         "model_path": self.files_edit.text().strip(), "preset": self.preset_combo.currentText().strip(),
                         "data_dir": self.data_edit.text().strip()}
        cfg["preset_name"] = self.name_edit.text().strip() or config.DEFAULTS["preset_name"]
        cfg["extra_flags"] = self.extra_edit.text().strip()
        cfg["own_scope"] = self.scope_switch.isChecked()
        return cfg

    def changed(self, *_):
        if self.loading:
            return
        self.dirty = True
        self.refresh_buttons()
        self.refresh_preview()

    def refresh_preview(self):
        cfg = self.collect()
        if not cfg.get("strata_command"):
            self.preview.setText("Choose where Strata is first.")
        else:
            self.preview.setText(command.printable(command.launch_command(cfg, self.opts)))
        self.refresh_strata_note()

    # ------------------------------------------------------------------ App tab actions
    def refresh_login_rows(self):
        on = self.sw_login.isChecked()
        self.row_minimized.setEnabled(on)
        self.row_server.setEnabled(on)

    def on_login_switch(self, on: bool):
        if self.loading:
            return
        try:
            if on:
                self.autostart.enable(autostart.launcher_command())
                self.say("The launcher will open when you sign in.")
            else:
                self.autostart.disable()
                self.say("The launcher will no longer open when you sign in.")
        except OSError as e:
            self.say(f"That could not be changed: {e}", "error")
            self.loading = True
            self.sw_login.setChecked(self.autostart.is_enabled())
            self.loading = False
        self.refresh_login_rows()

    def on_open_folder(self):
        paths.config_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.config_dir())))

    def on_open_options(self):
        path = paths.user_options_file()
        try:
            options.write_user_template(path)
        except OSError as e:
            self.say(f"The file could not be made: {e}", "error")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.say(f"Your settings list is {path}. Close the launcher and open it again after changing it.")

    def on_open_log(self):
        path = paths.log_file()
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.say("The launcher has not written a log yet.")

    # ----- updates -----
    def check_updates(self, by_hand: bool):
        """One web request in a background thread. A second check while one runs is not started."""
        if self.checking:
            return
        self.checking = True

        def work():
            info = updates.check()
            info["by_hand"] = by_hand
            self.bridge.update.emit(info)
        if by_hand:
            self.update_label.setText("Asking…")
        threading.Thread(target=work, daemon=True, name="update-check").start()

    def on_update(self, info: dict):
        self.checking = False
        self.cfg["last_update_check"] = int(time.time())
        self.save_cfg()
        self.update_info = None
        self.btn_get.setVisible(False)
        if not info.get("ok"):
            self.update_label.setText(f"You have version {__version__}. {html.escape(info.get('why', ''))}")
            if info.get("by_hand"):
                self.say(info.get("why", "The update check failed."), "warn")
            return
        if not info["newer"]:
            self.update_label.setText(f"You have version {__version__}. That is the newest.")
            return
        self.update_info = info
        page_link = f' <a href="{html.escape(info["page"])}">What is new</a>' if info.get("page") else ""
        self.update_label.setText(f"<b>Version {html.escape(info['latest'])} is available.</b> You have {__version__}.{page_link}")
        self.btn_get.setText("Download and install" if (sys.platform.startswith("win") and info.get("asset")) else "Open the download page")
        self.btn_get.setVisible(True)
        self.tabs.setTabText(TABS.index("app"), "App  •")
        if info["latest"] != self.cfg.get("skipped_version") or info.get("by_hand"):
            self.say(f"Version {info['latest']} of the launcher is available. See the App tab.")
            self.cfg["skipped_version"] = info["latest"]
            self.save_cfg()

    def on_get_update(self):
        info = self.update_info
        if not info:
            return
        if not (sys.platform.startswith("win") and info.get("asset")):
            QDesktopServices.openUrl(QUrl(info.get("page") or f"https://github.com/{GITHUB_REPO}/releases/latest"))
            return
        self.btn_get.setEnabled(False)
        self.update_label.setText(f"Downloading {html.escape(info['asset']['name'])}…")

        def work():
            try:
                folder = tempfile.mkdtemp(prefix="strata-launcher-update-")
                self.bridge.fetched.emit(updates.download(info["asset"], folder), "")
            except Exception as e:
                self.bridge.fetched.emit("", f"{type(e).__name__}: {e}")
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def on_fetched(self, path: str, error: str):
        self.btn_get.setEnabled(True)
        if error or not path:
            self.update_label.setText("The download did not finish. Try again, or use the download page.")
            self.say(f"The update could not be downloaded ({error}).", "error")
            return
        self.say("Installing the new version. The launcher closes and opens again. Strata keeps running.")
        try:
            popen(["msiexec", "/i", path, "/passive", "/norestart", "LAUNCHAPP=1"],
                  creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except OSError as e:
            self.say(f"The installer could not be started: {e}", "error")
            return
        QTimer.singleShot(300, QApplication.instance().quit)

    # ----- updating Strata itself, with Strata's own updater -----
    def check_strata_update(self, by_hand: bool):
        """Asks `strata update --check` in a background thread: one short program, never two at once."""
        cfg = dict(self.cfg)
        if not cfg.get("strata_command"):
            return
        if by_hand:
            self.strata_update_label.setText("Asking Strata's updater…")

        def work():
            try:
                _code, out = self.runner.capture(command.update_check_command(cfg), timeout=180, job="strata-update-check")
            except Busy:
                return
            self.bridge.strata_update.emit(strata.parse_update_check(out), by_hand)
        threading.Thread(target=work, daemon=True, name="strata-update-check").start()

    def on_strata_checked(self, found, by_hand: bool):
        self.cfg["last_strata_check"] = int(time.time())
        self.strata_found = found
        setup = TABS.index("setup")
        if found is None or not found["ok"]:
            why = (found or {}).get("problem") or "Strata's updater gave no answer. Is this PC online?"
            self.strata_update_label.setText(why)
            self.btn_strata_update.setVisible(False)
            self.tabs.setTabText(setup, "Setup")
            if by_hand:
                self.say(why, "warn")
            self.save_cfg()
            return
        self.strata_update_label.setText(strata.update_text(found) + (f" Strata says: {found['problem']}" if found["problem"] else ""))
        self.btn_strata_update.setVisible(found["available"])
        self.tabs.setTabText(setup, "Setup  •" if found["available"] else "Setup")
        if found["available"] and (by_hand or strata.update_key(found) != self.cfg.get("strata_update_seen")):
            self.say(strata.update_text(found) + " See the Setup tab.")
            self.cfg["strata_update_seen"] = strata.update_key(found)
        elif by_hand:
            self.say(strata.update_text(found))
        self.save_cfg()

    def on_update_strata(self, *_):
        """Runs Strata's own updater after a yes. A running model is only stopped when the person agrees to that too."""
        found = self.strata_found
        if self.busy is not None or not found or not found["available"] or not self.cfg.get("strata_command"):
            return
        loaded = self.running or found["serving"] > 0
        text = (strata.update_text(found) + "\n\nStrata's own updater downloads it from Strata's signed update feed"
                + (f" ({found['source']})" if found["source"] else "") + ", checks it, and switches only after a test start. "
                "If the update fails, the old version stays in use.")
        if loaded:
            text += ("\n\nA model is loaded. It must be stopped for the update: answers in progress stop. "
                     "Strata starts it again afterwards with the same settings.")
        text += "\n\nUpdate Strata now?"
        if QMessageBox.question(self, APP_NAME, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self.say("Strata was not updated.")
            return
        cfg = dict(self.cfg)
        self.was_ready = False
        self.say("Updating Strata with its own updater. Its messages follow.")

        def work():
            cmd = command.update_command(cfg, stop_model=loaded)
            if cfg.get("own_scope", True):                   # a model started again by the updater gets its own scope too
                cmd = own_scope(cmd, runner=self.runner)
            code = self.runner.stream(cmd, self.say_launch, job="launch")
            self.sampler.nudge()
            self.bridge.strata_update.emit(strata.parse_update_check(self.runner.capture(command.update_check_command(cfg), timeout=180)[1]), False)
            if code == 0:
                return True, "Strata is updated."
            if code == strata.UPDATE_REFUSED_MODEL_LOADED:
                return False, "Strata did not update because a model is loaded. Nothing was changed. Stop the model and try again."
            if code == strata.UPDATE_DID_NOT_START_AGAIN:
                return False, "Strata is updated, but the model did not start again. Press Start."
            return False, f"Strata did not update. Strata keeps the old version in use. Its messages are in the Log tab and in {paths.log_file()}"
        self.action("Updating Strata…", work)

    # ------------------------------------------------------------------ Save, Start, Stop
    def refresh_buttons(self):
        busy = self.busy is not None
        self.btn_save.setEnabled(not busy and self.dirty)
        self.btn_start.setEnabled(not busy)
        self.btn_start.setText("Restart with these settings" if self.running else "Start")
        self.btn_stop.setEnabled(not busy and self.running)
        self.btn_open.setEnabled(bool(self.sample.get("health")))
        h = self.sample.get("health") or {}
        self.btn_key.setVisible(bool(h.get("api_key")))
        self.btn_plan.setEnabled(not busy)
        self.btn_strata_update.setEnabled(not busy)
        self.btn_strata_check.setEnabled(not busy and bool(self.cfg.get("strata_command")))

    def on_save(self, *_):
        self.cfg = self.collect()
        self.save_cfg()
        self.dirty = False
        self.push_target()
        self.say("Settings saved. They take effect at the next Start.")
        self.refresh_buttons()

    def on_defaults(self):
        for key, (s, _w) in self.fields.items():
            self.set_field(key, s["default"])

    def can_run(self) -> bool:
        cfg = self.collect()
        if not cfg.get("strata_command"):
            self.say("Choose where Strata is first, on the Setup tab.", "warn")
            self.tabs.setCurrentIndex(TABS.index("setup"))
            return False
        why = command.source_problem(cfg["source"])
        if why:
            self.say(why, "warn")
            self.tabs.setCurrentIndex(TABS.index("setup"))
            return False
        return True

    def action(self, name: str, work) -> bool:
        """Runs one start, stop or plan in a background thread. A second one while the first runs is refused."""
        if self.busy is not None:
            return False
        self.busy = name
        self.show_status()

        def run():
            try:
                ok, message = work()
            except Busy:
                ok, message = False, "Another start or stop is still running."
            except Exception as e:                           # never leave the buttons greyed out
                ok, message = False, f"Something went wrong: {type(e).__name__}: {e}"
            self.bridge.done.emit(name, bool(ok), message or "")
        threading.Thread(target=run, daemon=True, name="action").start()
        return True

    def on_done(self, name: str, ok: bool, message: str):
        self.busy = None
        if name == "Stopping…":
            self.was_ready = False
            self.stopped_at = time.time() + 1.5
        if message:
            self.say(message, "launcher" if ok else "error")
        self.show_status()
        if name.startswith("Updating"):
            self.load_info()                                 # the version line on the Setup tab
        if not self.isActiveWindow():
            QApplication.alert(self)

    def _plan(self, cfg) -> dict | None:
        _code, out = self.runner.capture(command.plan_command(cfg, self.opts), timeout=120, job="plan")
        return strata.parse_plan(out)

    def on_plan(self):
        if not self.can_run():
            return
        cfg = self.collect()

        def work():
            plan = self._plan(cfg)
            if plan is None:
                return False, "Strata gave no plan for these choices. Try the command from the Setup tab in a terminal to see why."
            if not plan["ok"]:
                return False, f"Strata says: {plan['error']}"
            for w in plan["warnings"]:
                self.say_from_thread(f"Strata says: {w}", "warn")
            what = plan["title"] or "these choices"
            return True, f"Plan for {what}: {plan['text'] or 'nothing to download.'} Nothing was changed."
        self.action("Asking Strata…", work)

    def on_start(self, *_, confirmed: bool = False):
        if self.busy is not None or not self.can_run():
            return
        self.on_save()
        cfg = dict(self.cfg)
        url = self.url()
        if not confirmed:
            self.say("Starting. Strata is asked for its plan first.")

        def work():
            if not confirmed:
                plan = self._plan(cfg)
                if plan is not None and not plan["ok"]:
                    return False, f"Strata says: {plan['error']}"
                if plan is not None:
                    for w in plan["warnings"]:
                        self.say_from_thread(f"Strata says: {w}", "warn")
                    if plan["download_gb"] > 0:
                        self.bridge.ask.emit(plan)
                        return True, ""
            cmd = command.launch_command(cfg, self.opts)
            if cfg.get("own_scope", True):
                cmd = own_scope(cmd, runner=self.runner)
            code = self.runner.stream(cmd, self.say_launch, job="launch")
            if code != 0:
                return False, f"Strata did not start. The Log tab has its messages; the full output is in {paths.log_file()}"
            self.sampler.nudge()
            for _ in range(20):
                if http_json(url + "/health")[0] == 200:
                    return True, f"Strata is ready at {url}"
                time.sleep(0.5)
            return False, f"Strata started, but nothing answers at {url}. Check the port on the Settings tab."
        self.action("Starting…", work)

    def on_ask(self, plan: dict):
        """A start would download something: nothing is downloaded without a yes."""
        text = (f"Starting {plan['title'] or 'this model'} needs a download of about {plan['download_gb']:.1f} GB.\n\n"
                f"{plan['text']}\n\nDownload it now?")
        if QMessageBox.question(self, APP_NAME, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            QTimer.singleShot(0, lambda: self.on_start(confirmed=True))
        else:
            self.say("Nothing was downloaded and nothing was started.")

    def say_launch(self, line: str):
        """A line from `strata launch` while it starts or stops: its warnings stand out."""
        self.say_from_thread(line, "warn" if line.startswith("[!]") else "launcher")

    def on_stop(self, *_):
        if self.busy is not None or not self.cfg.get("strata_command"):
            return
        self.was_ready = False
        cfg = dict(self.cfg)

        def work():
            code = self.runner.stream(command.stop_command(cfg), self.say_launch, job="launch")
            if code == 0:
                return True, "Strata is stopped. Its memory is free again."
            return False, f"Strata did not stop cleanly. See the Log tab and {paths.log_file()}"
        self.action("Stopping…", work)

    def on_key(self):
        cfg = dict(self.cfg)
        if not cfg.get("strata_command"):
            return

        def work():
            try:
                _code, out = self.runner.capture(command.key_command(cfg), timeout=30, job="copy-key")
            except Busy:
                return
            self.bridge.key.emit(strata.parse_key(out) or "")
        threading.Thread(target=work, daemon=True, name="copy-key").start()

    def on_key_ready(self, key: str):
        if key:
            QGuiApplication.clipboard().setText(key)
        self.say("The API key is on the clipboard." if key else "Strata gave no API key for the running server.")

    # ------------------------------------------------------------------ live readings
    def on_ready(self):
        s, records, first = self.sampler.take()
        if records:
            self.log_add(records)
        if s is not None:
            found = s.get("engine_log")
            if found and found != self.cfg.get("engine_log"):
                self.cfg["engine_log"] = found
                self.save_cfg()
            self.refresh_live(s)
            if self.start_wanted:
                self.start_wanted = False
                if not s.get("health") and not s.get("engine_pid"):
                    self.say("Starting the server, as you asked for sign-in.")
                    self.on_start()

    def why_gone(self):
        """A plain sentence for a server that disappeared without Stop. Linux can say if memory ran out."""
        def work():
            text = "Strata stopped by itself. The Log tab may say why."
            if sys.platform.startswith("linux") and shutil.which("journalctl"):
                try:
                    _code, out = self.runner.capture(["journalctl", "-k", "--since", "-10min", "--no-pager", "-g", "Out of memory: Killed process .*strata"],
                                                     timeout=10, job="why")
                    if "Out of memory" in out:
                        text = "Strata was stopped by Linux because the PC ran out of memory. Close other programs, or choose a smaller context, and start again."
                except Busy:
                    pass
            self.say_from_thread(text, "error")
        threading.Thread(target=work, daemon=True, name="why").start()

    def show_status(self):
        s = self.sample
        state = live_state(s) if s else {"kind": "stopped"}
        now_running = state["kind"] != "stopped"
        if self.was_ready and not now_running and not self.busy:
            self.was_ready = False
            self.why_gone()
        # "Ready" is only noted from a reading taken after the last Stop finished: the reading from the second
        # before would still show the server and make a wanted stop look like a crash.
        if state["kind"] in ("idle", "busy") and not self.busy and s.get("at", 0) > self.stopped_at:
            self.was_ready = True
        self.running = now_running
        url, c = self.url(), self.colours
        if self.busy:
            waiting = ("Strata is downloading and testing its new version. This can take several minutes."
                       if self.busy.startswith("Updating") else "Please wait. Loading a model can take a minute or more.")
            title, sub, colour = self.busy, waiting, c["warning"]
        elif state["kind"] in ("idle", "busy"):
            title = (state["phase"].capitalize() + "…") if state["kind"] == "busy" else "Running"
            sub = f'{html.escape(state["model"])} at <a href="{html.escape(url)}/" style="color:{c["accent"]}">{html.escape(url)}</a>'
            if self.cfg["values"].get("access") not in (None, "", "local"):
                sub += "  ·  open beyond this computer"
            if state["api_key"]:
                sub += "  ·  API key needed"
            colour = c["accent"] if state["kind"] == "busy" else c["ok"]
        elif state["kind"] == "loading":
            title, sub, colour = "Starting or stopping", "The engine is in memory but does not answer yet.", c["warning"]
        elif not self.cfg.get("strata_command"):
            title, sub, colour = "Not set up yet", "Open the Setup tab and choose where Strata is.", c["dim"]
        else:
            title, sub, colour = "Stopped", "Press Start to load the model.", c["dim"]
        self.status_title.setText(title)
        if self.shown_sub != sub:                            # setting the same text again would drop a text selection
            self.shown_sub = sub
            self.status_sub.setText(sub)
        self.dot.set_colour(colour)
        self.refresh_buttons()

    def refresh_idle_speed(self, summary):
        if summary:
            self.speed_big.setText(f"{summary['last']['tps']:.1f} tok/s")
            self.speed_small.setText(f"last answer · average {summary['avg']:.1f}")
        else:
            self.speed_big.setText("")
            self.speed_small.setText("")

    def refresh_answers(self, s: dict):
        """The parts that change when an answer finishes or the engine restarts."""
        summary, run = s.get("answers"), s.get("run")
        up = bool(s.get("health"))
        if summary:
            source = "the engine's log" if s.get("answers_from_log") else "the server's list of recent requests"
            self.perf_note.setText(("Since this start. " if up else "From the last run. Strata is stopped now. ")
                                   + f"Read from {source}. Prompts under 200 tokens are left out of prompt reading.")
            last, hist = summary["last"], summary["history"]
            vals = {"last": (f"{last['tps']:.1f}", f"tok/s · {last['out']:,} tokens"), "avg": (f"{summary['avg']:.1f}", "tok/s, all answers"),
                    "best": (f"{summary['best']:.1f}", "tok/s"),
                    "read": (f"{summary['read']:,.0f}" if summary["read"] else "-", "tok/s, last long prompt"),
                    "count": (f"{summary['count']:,}", "in this run"), "tokens": (f"{summary['tokens']:,}", "in this run"),
                    "drafts": (f"{summary['drafts']:.0f}%" if summary["drafts"] is not None else "-", "last answer"),
                    "hit": (f"{summary['hit']:.1f}%" if summary["hit"] is not None else "-",
                            f"1st card; {summary['other']:.0f}% from elsewhere" if summary["other"] is not None else "1st card")}
            self.chart_caption.setText(f"Speed of the last {plural(len(hist), 'answer')}, newest on the right"
                                       f"   ·   highest {max(hist):.0f} tok/s   ·   lowest {min(hist):.0f} tok/s")
            self.chart.set_data(hist, self.colours)
        else:
            self.perf_note.setText("No answer yet in this run. Send the model a request and the numbers appear here.")
            vals = {k: ("-", "") for k in self.tiles}
            self.chart_caption.setText("")
        self.chart.setVisible(bool(summary))
        self.chart_caption.setVisible(bool(summary))
        for k, tile in self.tiles.items():
            tile.set(*vals[k])
        eng = (s.get("metrics") or {}).get("engine") or {}
        version = getattr(run, "engine", None) or eng.get("engine") or eng.get("version")
        started = getattr(run, "started", None)
        self.run_rows["engine"].setText((str(version) if version else "-") + (f", started {started}" if started else ""))
        context = getattr(run, "context", None) or eng.get("max_context")
        slots = getattr(run, "slots", None) if getattr(run, "context", None) else eng.get("conversation_cache_slots")
        self.run_rows["context"].setText(f"{context // 1024}K tokens" + (f", {plural(slots, 'slot')}" if slots else "") if context else "-")
        parts = [f"{n} {g:.1f}" for g, n in ((getattr(run, "gpu0_gib", None), "1st card"), (getattr(run, "gpu1_gib", None), "2nd card"),
                                             (getattr(run, "ram_gib", None), "RAM")) if g]
        total = getattr(run, "total_gib", None)
        self.run_rows["model"].setText(((f"{total:.1f} GiB = " if total else "") + " + ".join(parts) + " GiB") if parts else "-")
        pcie = getattr(run, "pcie", None)
        self.run_rows["link"].setText(f"{pcie:.1f} GB/s" if pcie else "-")
        checks = getattr(run, "checks", 0)
        self.run_rows["watch"].setText(f"{checks:,} one-token checks, not counted as answers" if checks else "-")
        return summary

    def refresh_live(self, s: dict):
        """Once a second, with the newest reading."""
        self.sample = s
        self.show_status()
        state = live_state(s)
        summary = self.refresh_answers(s)
        gpus = cards(s)
        bits = [f"{g['name']}: {g['used'] / 1024:.1f} of {g['total'] / 1024:.0f} GiB" for g in gpus if g.get("used") is not None]
        if s.get("ram_avail") is not None:
            bits.append(f"RAM free: {s['ram_avail']:.0f} of {s['ram_total']:.0f} GiB")
        self.memory_line.setText("   ·   ".join(bits))
        self.cards_label.setText("\n".join(f"Card {g['index']}: {g['name']}, {g['total'] / 1024:.0f} GiB" for g in gpus)
                                 or "No card was reported. Cards show here when the NVIDIA tool nvidia-smi is installed, or while Strata runs.")

        # "Right now" tiles
        t = self.now_tiles
        if state["kind"] in ("stopped", "loading"):
            t["state"].set("Stopped" if state["kind"] == "stopped" else "Loading", "")
            t["speed"].set("-", "")
            self.refresh_idle_speed(summary)
        elif state["kind"] == "busy":
            phase = state["phase"].capitalize()
            slots = f"{state['running']} of {state['slots'] or '?'} slots busy" + (f", {state['queued']} waiting" if state["queued"] else "")
            t["state"].set(phase, slots)
            if state["generated"]:
                tps = state["tok_s"]
                t["speed"].set(f"{tps:.1f}" if tps else "-", f"tok/s · {state['generated']:,} tokens in {state['elapsed']:.0f} s")
                self.speed_big.setText(f"{tps:.1f} tok/s" if tps else "…")
                self.speed_small.setText(f"{phase.lower()} · {state['generated']:,} tokens · {state['elapsed']:.0f} s")
                if time.time() - self.live_logged >= 5:      # a line in the log every 5 s while it writes
                    self.live_logged = time.time()
                    self.log_add([("live", f"{time.strftime('%H:%M:%S')}  … {phase.lower()}: {state['generated']:,} tokens so far at "
                                           f"{tps or 0:.1f} tok/s ({state['elapsed']:.0f} s)")])
            else:
                done, total = state["prompt_done"], state["prompt_total"]
                prog = f"{done:,} of {total:,} tokens" if done is not None and total else ""
                t["speed"].set("…", ("prompt: " + prog) if prog else phase.lower())
                self.speed_big.setText("…")
                self.speed_small.setText(phase.lower() + (f" · {prog}" if prog else ""))
        else:
            note = (f"{plural(state['slots'], 'slot')} free" if state["slots"] else "ready") if state["has_metrics"] else \
                   ("ready (live figures need the API key)" if state["denied"] else "ready")
            t["state"].set("Idle", note)
            t["speed"].set("-", "waiting for a request")
            self.refresh_idle_speed(summary)
        d = s.get("disk_mb_s")
        t["disk"].set(f"{d:,.0f}" if d is not None else "-", "MB/s read by the engine" if d is not None else "")
        so, si = s.get("swap_out_mb_s"), s.get("swap_in_mb_s")
        used_swap = (s["swap_total"] - s["swap_free"]) if s.get("swap_total") else None
        t["swap"].set(f"{(so or 0) + (si or 0):,.0f}" if so is not None else ("-" if used_swap is None else f"{used_swap:.1f}"),
                      (f"MB/s · {used_swap:.1f} of {s['swap_total']:.0f} GiB in use" if so is not None else f"GiB of {s['swap_total']:.0f} in use")
                      if used_swap is not None else "")
        self.run_rows["disk"].setText(f"{s['disk_total_gb']:,.1f} GB" if s.get("disk_total_gb") is not None else "-")

        self.refresh_memory(s)
        self.refresh_hardware(s, gpus)

    def refresh_memory(self, s: dict):
        c = self.colours
        seen = set()
        for name, total, segs in memory_picture(s, s.get("run")):
            seen.add(name)
            if name not in self.mem_bars:
                title, bar, legend = label("", "rowTitle"), SegmentBar(), label("", "caption", wrap=True, rich=True)
                at = self.mem_box.count() if name == "RAM" else sum(3 for n in self.mem_bars if n != "RAM")
                for i, w in enumerate((title, bar, legend)):
                    self.mem_box.insertWidget(at + i, w)
                self.mem_bars[name] = (title, bar, legend)
            title, bar, legend = self.mem_bars[name]
            title.setText(f"{html.escape(name)}  ·  {total:.1f} GiB")
            bar.set_data(total, segs, c)
            legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(f'<span style="color:{c[key]}">■</span> {html.escape(lab)}: {amount:.1f} GiB' for lab, amount, key in segs))
        for name, widgets in self.mem_bars.items():
            for w in widgets:
                w.setVisible(name in seen)
        files = s.get("eng_files") or 0.0
        self.mem_note.setText("The cards as their driver reports them, the model's parts from the engine, the RAM from your system. Updated every second."
                              + (f" The engine also keeps {files:.1f} GiB of the model files in memory (yellow). The system drops them when memory is short, "
                                 "and then they are read from the drive again." if files > 2 else ""))

    def meter(self, key: str, title: str, order: tuple = (9,)) -> Meter:
        """The hardware row with this key. A new row is put in its place: cards first, then processor, RAM, swap, drives."""
        if key not in self.hw_rows:
            self.hw_rows[key] = Meter(title)
            self.hw_order[key] = order
            box = self.hw_card.box
            for i in reversed(range(box.count())):           # take the rows out (the heading stays) and put them back in order
                item = box.itemAt(i)
                w = item.widget()
                if w is not None and (isinstance(w, Meter) or w.objectName() == "line"):
                    box.takeAt(i)
                    if not isinstance(w, Meter):
                        w.deleteLater()
            self.hw_card.rows = 0
            for k in sorted(self.hw_rows, key=lambda k: self.hw_order[k]):
                self.hw_card.add(self.hw_rows[k])
        return self.hw_rows[key]

    def refresh_hardware(self, s: dict, gpus: list):
        for g in gpus:
            if g.get("used") is not None:
                self.meter(f"gpu{g['index']}m", f"{g['name']} memory", (0, g["index"], 0)).set(g["used"] / g["total"], f"{g['used'] / 1024:.1f} of {g['total'] / 1024:.1f} GiB")
            extra = [f"{g['temp']:.0f} °C" if g.get("temp") is not None else "", f"{g['power']:.0f} W" if g.get("power") is not None else ""]
            if g.get("util") is not None:
                self.meter(f"gpu{g['index']}u", f"{g['name']} load", (0, g["index"], 1)).set(g["util"] / 100.0, f"{g['util']:.0f}%", "  ·  ".join(x for x in extra if x))
        if s.get("cpu_pct") is not None:
            name = ((s.get("metrics") or {}).get("hardware_static") or {}).get("cpu_name")
            self.meter("cpu", "Processor load", (1,)).set(s["cpu_pct"] / 100.0, f"{s['cpu_pct']:.0f}%", str(name) if name else None)
        if s.get("ram_total"):
            used = s["ram_total"] - s["ram_avail"]
            eng = (s.get("eng_locked") or 0) + (s.get("eng_work") or 0)
            note = (f"Strata's engine: {eng:.1f} GiB" + (f", plus {s['eng_files']:.1f} GiB of model files kept in memory" if (s.get("eng_files") or 0) > 0.1 else "")
                    if s.get("engine_pid") else "Strata's engine is not running")
            self.meter("ram", "RAM in use", (2,)).set(used / s["ram_total"], f"{used:.1f} of {s['ram_total']:.1f} GiB", note)
        if s.get("swap_total"):
            used = s["swap_total"] - s["swap_free"]
            self.meter("swap", "Swap in use", (3,)).set(used / s["swap_total"], f"{used:.1f} of {s['swap_total']:.1f} GiB",
                                                  "Memory that programs had to give up to the drive." if used > 1 else "")
        for d in s.get("drives") or []:
            if d.get("temp") is None:
                continue
            note = f"hottest sensor inside: {d['hottest']:.0f} °C"
            if d.get("warn") and d.get("crit"):
                note += f"  ·  the drive slows down at {d['warn']:.0f} °C and shuts off at {d['crit']:.0f} °C"
            self.meter(f"drv-{d['id']}", f"{d['model']} temperature" + (" (holds the model)" if d["holds_model"] else ""), (4, d["id"])).set(
                d["temp"] / (d.get("crit") or 90), f"{d['temp']:.0f} °C", note)
            hot = (d.get("warn") and d["temp"] >= d["warn"] - 8) or d["hottest"] >= 85
            if d["holds_model"] and hot and time.time() - self.hot_said > 120:
                self.hot_said = time.time()
                self.log_add([("warn", f"{time.strftime('%H:%M:%S')}  The drive with the model files is hot: {d['temp']:.0f} °C overall, "
                                       f"{d['hottest']:.0f} °C at its hottest sensor. Heavy reading from it is what heats it.")])

    # ------------------------------------------------------------------ closing
    def closeEvent(self, event):
        self.sampler.stop()
        if not self.isMaximized() and not self.isMinimized():
            self.cfg["window"] = {"width": self.width(), "height": self.height()}
        self.cfg["log"] = {"details": self.chk_details.isChecked(), "live": self.chk_live.isChecked(), "follow": self.chk_follow.isChecked()}
        try:
            saved, _notes, existed = config.load()
            if existed:                                      # only the window's own fields: unsaved boxes stay unsaved
                saved["window"], saved["log"] = self.cfg["window"], self.cfg["log"]
                config.save(saved)
        except OSError:
            pass
        super().closeEvent(event)
