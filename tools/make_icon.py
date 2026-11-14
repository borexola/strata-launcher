#!/usr/bin/env python3
"""Draws the launcher's icon files from assets/icon.svg: icon.png (256 px) and icon.ico (several sizes).

The icon is this project's own drawing. Run it again after changing icon.svg:
    QT_QPA_PLATFORM=offscreen python tools/make_icon.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "src" / "strata_launcher" / "assets"


def render(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(data)


def main() -> int:
    app = QGuiApplication(sys.argv)  # noqa: F841  (Qt needs it for drawing)
    renderer = QSvgRenderer(str(ASSETS / "icon.svg"))
    (ASSETS / "icon.png").write_bytes(render(renderer, 256))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [render(renderer, s) for s in sizes]
    # An .ico file: a 6-byte header, one 16-byte entry per picture, then the pictures (PNG data is allowed).
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (ASSETS / "icon.ico").write_bytes(out + b"".join(images))
    print("wrote icon.png and icon.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
