"""Рисует иконку (белый микрофон на чёрном) в assets/voice.ico и кладёт ярлык на рабочий стол.

Запуск: .venv\\Scripts\\python.exe tools\\make_shortcut.py
"""

import base64
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "assets" / "voice.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
NAME = "Голосовой ввод"


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size, size)  # дальше всё в долях стороны

    # Фон: чёрный скруглённый квадрат.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#000000"))
    p.drawRoundedRect(QRectF(0.02, 0.02, 0.96, 0.96), 0.22, 0.22)

    white = QColor("#FFFFFF")
    # Капсула микрофона.
    p.setBrush(white)
    p.drawRoundedRect(QRectF(0.38, 0.15, 0.24, 0.39), 0.12, 0.12)

    # Дужка, ножка и подставка — линиями; на мелких размерах линия не тоньше 1,5 px.
    pen = QPen(white, max(0.06, 1.5 / size))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath(QPointF(0.29, 0.42))
    arc.arcTo(QRectF(0.29, 0.21, 0.42, 0.42), 180, 180)
    p.drawPath(arc)
    p.drawLine(QPointF(0.5, 0.63), QPointF(0.5, 0.77))
    p.drawLine(QPointF(0.39, 0.77), QPointF(0.61, 0.77))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    """ICO с PNG внутри — Windows Vista+ понимает такие для всех размеров."""
    blobs = [png_bytes(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(header) + 16 * len(blobs)
    entries = b""
    for img, blob in zip(images, blobs):
        side = img.width() if img.width() < 256 else 0
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(header + entries + b"".join(blobs))


def make_shortcut() -> None:
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    script = f"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$desktop =[Environment]::GetFolderPath('Desktop')
$link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop '{NAME}.lnk'))
$link.TargetPath = '{pythonw}'
$link.Arguments = '"{ROOT / "run.pyw"}"'
$link.WorkingDirectory = '{ROOT}'
$link.IconLocation = '{ICON},0'
$link.Description = 'Голосовой ввод: дважды \\ или клики ЛКМ-ПКМ-ЛКМ'
$link.Save()
Write-Output $link.FullName
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode:
        sys.exit(out.stderr)
    print(out.stdout.strip())


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    images = [render(s) for s in SIZES]
    write_ico(ICON, images)
    print(ICON)
    if "--icon-only" not in sys.argv:
        make_shortcut()
