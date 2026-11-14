"""The small custom pieces of the window: the on/off switch, the memory bar, the speed chart, tiles and rows."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class NoWheel(QObject):
    """Stops the mouse wheel from changing a value while the page is being scrolled.

    A box only reacts to the wheel after it was clicked.
    """

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


class Switch(QAbstractButton):
    """An on/off switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.colours = {"accent": "#1c6fd6", "track": "#c8c8c4", "card": "#ffffff"}

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QColor(self.colours["accent"] if on else self.colours["track"])
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        r = QRectF(1, 2, self.width() - 2, self.height() - 4)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if on else r.left() + 3
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            pen = QColor(self.colours["accent"])
            p.setPen(pen)
            p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), r.height() / 2, r.height() / 2)


class SegmentBar(QWidget):
    """One bar split into coloured parts, for "where the memory goes"."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 0.0
        self.segments: list[tuple[str, float, str]] = []
        self.colours: dict = {}
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)

    def set_data(self, total: float, segments, colours: dict):
        self.total, self.segments, self.colours = total, list(segments), colours
        self.update()

    def paintEvent(self, _event):
        if not self.total:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h, x = self.width(), self.height(), 0.0
        for _label, amount, key in self.segments:
            width = w * amount / self.total
            p.setBrush(QColor(self.colours.get(key, "#888888")))
            p.drawRoundedRect(QRectF(x, 2, max(1.0, width - 2), h - 4), 4, 4)
            x += width


class SpeedChart(QWidget):
    """The speed of the last answers as bars, newest on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history: list[float] = []
        self.colours: dict = {}
        self.setMinimumHeight(96)
        self.setMaximumHeight(96)

    def set_data(self, history, colours: dict):
        self.history, self.colours = list(history), colours
        self.update()

    def paintEvent(self, _event):
        if not self.history:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        top = max(self.history) * 1.15 or 1.0
        gap, n = 3, max(len(self.history), 12)
        bw = min(34.0, max(3.0, (w - gap * (n - 1)) / n))
        guide = QColor(self.colours.get("border", "#cccccc"))
        for frac in (0.5, 1.0):                              # two faint guide lines: half and full of the highest
            p.setBrush(guide)
            p.drawRect(QRectF(0, h - h * frac / 1.15, w, 1))
        bar = QColor(self.colours.get("bar", "#3584e4"))
        for i, v in enumerate(self.history):
            bh = h * v / top
            c = QColor(bar)
            c.setAlphaF(1.0 if i == len(self.history) - 1 else 0.55)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(i * (bw + gap), h - bh, bw, bh), 2, 2)


class Dot(QWidget):
    """The round status light."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colour = "#888888"
        self.setFixedSize(16, 16)

    def set_colour(self, colour: str):
        if colour != self.colour:
            self.colour = colour
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.colour))
        p.drawEllipse(QRectF(2, 2, 12, 12))


def label(text="", name="", wrap=False, selectable=False, rich=False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if rich:
        lab.setTextFormat(Qt.RichText)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
    return lab


def line() -> QFrame:
    f = QFrame()
    f.setObjectName("line")
    f.setFixedHeight(1)
    return f


class Card(QFrame):
    """A rounded box with an optional heading, holding rows one under the other."""

    def __init__(self, title: str = "", note: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 12, 16, 12)
        self.box.setSpacing(10)
        self.rows = 0
        if title or note:
            head = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            if title:
                col.addWidget(label(title, "heading"))
            if note:
                col.addWidget(label(note, "help", wrap=True))
            head.addLayout(col, 1)
            self.head = head
            self.box.addLayout(head)
        else:
            self.head = None

    def add_header_widget(self, w: QWidget):
        if self.head is not None:
            self.head.addWidget(w, 0, Qt.AlignTop)

    def add(self, w: QWidget, divider: bool = True):
        if divider and (self.rows or self.head is not None):
            self.box.addWidget(line())
        self.box.addWidget(w)
        self.rows += 1

    def add_row(self, title: str, help_text: str, control: QWidget | None, control_width: int = 250) -> QWidget:
        row = Row(title, help_text, control, control_width)
        self.add(row)
        return row


class Row(QWidget):
    """A title and a help line on the left, a control on the right."""

    def __init__(self, title: str, help_text: str = "", control: QWidget | None = None, control_width: int = 250, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(title, "rowTitle", wrap=True)
        self.help = label(help_text, "help", wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.help)
        self.help.setVisible(bool(help_text))
        lay.addLayout(col, 1)
        if control is not None:
            if control_width and not isinstance(control, Switch):
                control.setFixedWidth(control_width)
            lay.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)

    def set_help(self, text: str):
        self.help.setText(text)
        self.help.setVisible(bool(text))


class Tile(QFrame):
    """A small box with a title, one big figure and a line under it."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("tile")
        self.setMinimumWidth(130)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(2)
        self.title = label(title, "tileTitle")
        self.value = label("-", "big")
        self.unit = label("", "caption", wrap=True)
        for w in (self.title, self.value, self.unit):
            w.setAlignment(Qt.AlignHCenter)
            lay.addWidget(w)
        self.unit.setMinimumHeight(30)
        self.unit.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

    def set(self, value: str, unit: str = ""):
        if self.value.text() != value:
            self.value.setText(value)
        if self.unit.text() != unit:
            self.unit.setText(unit)


class Meter(QWidget):
    """One hardware line: a name, a small note, a bar and a figure."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = label(title, "rowTitle")
        self.note = label("", "help", wrap=True)
        self.note.setVisible(False)
        col.addWidget(self.title)
        col.addWidget(self.note)
        lay.addLayout(col, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(220)
        self.value = label("-")
        self.value.setMinimumWidth(130)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.bar, 0, Qt.AlignVCenter)
        lay.addWidget(self.value, 0, Qt.AlignVCenter)

    def set(self, fraction: float | None, value: str, note: str | None = None):
        if fraction is not None:
            self.bar.setValue(int(1000 * max(0.0, min(1.0, fraction))))
        if self.value.text() != value:
            self.value.setText(value)
        if note is not None and note != self.note.text():
            self.note.setText(note)
            self.note.setVisible(bool(note))
