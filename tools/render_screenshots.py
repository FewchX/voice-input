"""Скриншоты для README: docs/screenshot-*.png.

Сцена синтетическая — условный мессенджер на тёмных обоях, текст выдуманный. Окна на экран
не выводятся: остров и редактор рисуются прямо в картинку (в 2× для чёткости).
Запуск: .venv\\Scripts\\python.exe tools\\render_screenshots.py
"""

import math
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)

from voice_input.themes import THEMES, Backdrop, blur, with_glow  # noqa: E402
from voice_input.ui import SHADOW, TOP_GAP, Editor, Island  # noqa: E402

W, H = 1280, 860
SCALE = 2
THEME = with_glow(THEMES["dark_glass"], None)

DICTATED = (
    "Посмотрел пулл-реквест, всё хорошо. Пару мелких правок залил в git, "
    "а тесты прогнал через Claude Code — всё зелёное. Можно вливать"
)
CHAT = [
    (False, "Всем привет! Кто сможет посмотреть пулл-реквест до вечера?"),
    (True, "Я гляну после обеда"),
    (False, "Отлично, спасибо 🙌"),
]
CONTACTS = [
    ("К", "Команда проекта", "Отлично, спасибо", "#5B7CFA"),
    ("А", "Анна", "Созвон в 15:00?", "#E07A5F"),
    ("М", "Максим", "Скинул макеты", "#3DAA8C"),
    ("Д", "Дизайн", "Новая палитра готова", "#B07CE0"),
    ("Л", "Лена", "Спасибо!", "#D9A441"),
    ("С", "Сергей", "Ок, завтра обсудим", "#4FA3C7"),
]


class FakeEngine:
    level = 0.6


def ui_font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont("Segoe UI Variable Text")
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


# --------------------------------------------------------------------------- фон


def wallpaper(p: QPainter) -> None:
    g = QLinearGradient(0, 0, W, H)
    g.setColorAt(0, QColor("#1A2442"))
    g.setColorAt(0.55, QColor("#2E2550"))
    g.setColorAt(1, QColor("#4A2E45"))
    p.fillRect(QRectF(0, 0, W, H), g)
    for color, fx, fy, r in (("#3E6FD8", 0.18, 0.12, 0.32), ("#8A4FD0", 0.82, 0.18, 0.28), ("#D0705A", 0.7, 0.95, 0.3)):
        c = QPointF(W * fx, H * fy)
        rg = QRadialGradient(c, W * r)
        col = QColor(color)
        col.setAlpha(150)
        rg.setColorAt(0, col)
        col.setAlpha(0)
        rg.setColorAt(1, col)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rg)
        p.drawEllipse(c, W * r, W * r)


def bubble(p: QPainter, text: str, mine: bool, right: float, left: float, y: float) -> float:
    """Пузырь сообщения; возвращает его нижний край."""
    p.setFont(ui_font(10.5))
    max_w = 420
    br = p.fontMetrics().boundingRect(QRect(0, 0, max_w, 1000), Qt.TextFlag.TextWordWrap, text)
    w, h = br.width() + 28, br.height() + 18
    x = right - w if mine else left
    rect = QRectF(x, y, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#3A5BD9") if mine else QColor("#2C2D33"))
    p.drawRoundedRect(rect, 14, 14)
    p.setPen(QColor("#F2F3F7"))
    p.drawText(rect.adjusted(14, 9, -14, -9), Qt.TextFlag.TextWordWrap, text)
    return y + h


def chat_window(p: QPainter) -> None:
    win = QRectF(110, 250, W - 220, H - 290)
    # тень окна
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(18, 0, -1):
        p.setBrush(QColor(0, 0, 0, int(40 * (1 - i / 18) ** 2)))
        p.drawRoundedRect(win.adjusted(-i, -i * 0.6, i, i * 1.6), 10 + i, 10 + i)
    p.setBrush(QColor("#1C1D21"))
    p.drawRoundedRect(win, 10, 10)

    # заголовок окна с кнопками свернуть / развернуть / закрыть
    p.setFont(ui_font(9))
    p.setPen(QColor("#9A9CA6"))
    p.drawText(QRectF(win.left() + 16, win.top(), 200, 34), Qt.AlignmentFlag.AlignVCenter, "Чат")
    pen = QPen(QColor("#9A9CA6"), 1.1)
    p.setPen(pen)
    cy = win.top() + 17
    xr = win.right() - 24
    p.drawLine(QPointF(xr - 5, cy - 5), QPointF(xr + 5, cy + 5))
    p.drawLine(QPointF(xr - 5, cy + 5), QPointF(xr + 5, cy - 5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(xr - 50, cy - 5, 10, 10))
    p.drawLine(QPointF(xr - 100, cy), QPointF(xr - 90, cy))

    body = win.adjusted(0, 34, 0, 0)
    side = QRectF(body.left(), body.top(), 270, body.height())
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#17181B"))
    p.drawRoundedRect(side.adjusted(0, 0, 10, 0), 10, 10)
    p.drawRect(side.adjusted(0, 0, 0, -12))
    p.setBrush(QColor("#25262B"))
    p.drawRoundedRect(QRectF(side.left() + 14, side.top() + 10, side.width() - 28, 32), 16, 16)
    p.setFont(ui_font(9.5))
    p.setPen(QColor("#73757F"))
    p.drawText(QRectF(side.left() + 30, side.top() + 10, 200, 32), Qt.AlignmentFlag.AlignVCenter, "Поиск")

    y = side.top() + 56
    for i, (letter, name, preview, color) in enumerate(CONTACTS):
        row = QRectF(side.left() + 8, y, side.width() - 16, 58)
        if i == 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#2A2C33"))
            p.drawRoundedRect(row, 10, 10)
        c = QPointF(row.left() + 30, row.center().y())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(c, 20, 20)
        p.setPen(QColor("white"))
        p.setFont(ui_font(11, QFont.Weight.DemiBold))
        p.drawText(QRectF(c.x() - 20, c.y() - 20, 40, 40), Qt.AlignmentFlag.AlignCenter, letter)
        p.setFont(ui_font(10, QFont.Weight.DemiBold))
        p.setPen(QColor("#E8E9EE"))
        p.drawText(QRectF(row.left() + 62, row.top() + 9, 180, 20), Qt.AlignmentFlag.AlignVCenter, name)
        p.setFont(ui_font(9))
        p.setPen(QColor("#80838D"))
        p.drawText(QRectF(row.left() + 62, row.top() + 30, 180, 18), Qt.AlignmentFlag.AlignVCenter, preview)
        y += 62

    main = QRectF(side.right(), body.top(), body.right() - side.right(), body.height())
    p.setPen(QColor("#2A2B30"))
    p.drawLine(QPointF(main.left(), main.top() + 56), QPointF(main.right(), main.top() + 56))
    p.setFont(ui_font(11.5, QFont.Weight.DemiBold))
    p.setPen(QColor("#ECEDF2"))
    p.drawText(QRectF(main.left() + 24, main.top() + 6, 400, 26), Qt.AlignmentFlag.AlignVCenter, "Команда проекта")
    p.setFont(ui_font(9))
    p.setPen(QColor("#80838D"))
    p.drawText(QRectF(main.left() + 24, main.top() + 30, 400, 18), Qt.AlignmentFlag.AlignVCenter, "5 участников")

    y = main.top() + 250
    for mine, text in CHAT:
        y = bubble(p, text, mine, main.right() - 24, main.left() + 24, y) + 10

    field = QRectF(main.left() + 20, main.bottom() - 62, main.width() - 40, 44)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#26272D"))
    p.drawRoundedRect(field, 22, 22)
    p.setFont(ui_font(10.5))
    p.setPen(QColor("#73757F"))
    p.drawText(field.adjusted(22, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Сообщение")
    p.setPen(QPen(QColor("#DADCE4"), 1.4))
    p.drawLine(QPointF(field.left() + 20, field.top() + 13), QPointF(field.left() + 20, field.bottom() - 13))


def desktop() -> QImage:
    img = QImage(W * SCALE, H * SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    wallpaper(p)
    chat_window(p)
    p.end()
    return img


# --------------------------------------------------------------------------- наш интерфейс


def island(text: str, armed: str = "", seconds: int = 9) -> Island:
    w = Island(FakeEngine(), THEME)
    w.move((W - w.width()) // 2, TOP_GAP - SHADOW)
    w._status = "listening"
    w._phase = 1.7
    w._t0 = time.monotonic() - seconds
    w._levels = deque(
        [max(0.04, 0.45 + 0.5 * math.sin(i * 0.55) * math.sin(i * 0.23 + 1)) for i in range(Island.BARS)],
        maxlen=Island.BARS,
    )
    w.set_text(text)
    w._armed = armed
    w._h = float(w._target_height())
    w._appear = 1.0
    return w


def editor(text: str) -> Editor:
    w = Editor(FakeEngine(), theme=THEME)
    w.edit.setPlainText(text)
    final = w._final_geometry()
    w.setGeometry(QRect(QPoint((W - final.width()) // 2, TOP_GAP - SHADOW), final.size()))
    w._layout_content(final.size())
    w.content.layout().activate()
    w.content.setGraphicsEffect(None)  # эффект прозрачности при render() второй раз сдвигает содержимое
    w._phase = 2.4
    return w


def compose(scene: QImage, widget: QWidget, crop: QRect | None = None) -> QImage:
    """Сцена + наш виджет поверх неё (стекло берёт размытый снимок сцены, как в приложении)."""
    Backdrop.set(blur(QPixmap.fromImage(scene), THEME.blur), QPoint(0, 0))
    # Отдельный прозрачный слой: render() через чужой QPainter дважды сдвигает дочерние виджеты.
    layer = QImage(widget.size() * SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(SCALE)
    layer.fill(Qt.GlobalColor.transparent)
    widget.render(layer, QPoint(0, 0), renderFlags=QWidget.RenderFlag.DrawChildren)
    img = scene.copy()
    img.setDevicePixelRatio(SCALE)
    p = QPainter(img)
    p.drawImage(widget.pos(), layer)
    p.end()
    if crop is not None:
        img = img.copy(QRect(crop.topLeft() * SCALE, crop.size() * SCALE))
        img.setDevicePixelRatio(SCALE)
    return img


def save(img: QImage, name: str) -> None:
    out = ROOT / "docs" / name
    out.parent.mkdir(exist_ok=True)
    img.save(str(out))
    print(out, f"{out.stat().st_size // 1024} КБ")


def main() -> None:
    scene = desktop()
    save(compose(scene, island(DICTATED)), "screenshot-dictation.png")
    top = QRect((W - 760) // 2, 0, 760, 190)
    save(compose(scene, island(DICTATED + ".", armed="send", seconds=12), top), "screenshot-send.png")
    save(compose(scene, editor(DICTATED + ".")), "screenshot-editor.png")


if __name__ == "__main__":
    main()
