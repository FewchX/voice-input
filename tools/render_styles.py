"""Рисует все стили (остров + редактор) на одной картинке: docs/styles.png.

С именем стиля в аргументе рисует только его — на ярком, тёмном и светлом фоне:
docs/<стиль>.png. С --glows — все цвета свечения «Тёмного стекла»: docs/glows.png. Окна на экран не выводятся — рендерим виджеты прямо в изображение.
Запуск: .venv\\Scripts\\python.exe tools\\render_styles.py
"""

import math
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from voice_input.themes import GLOWS, THEMES, Backdrop, blur, with_glow  # noqa: E402
from voice_input.ui import Editor, Island  # noqa: E402

TILE_W, TILE_H = 760, 560
SAMPLE = "Привет. Ну вот это вот сейчас я говорю, и интересно, насколько хорошо он будет считывать мой голос."


class FakeEngine:
    level = 0.55


def wallpaper(w: int, h: int, seed: int) -> QPixmap:
    """Яркие «обои» с размытыми пятнами и окном документа — чтобы было видно стекло и тени."""
    pm = QPixmap(w, h)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#3B6FD8"))
    g.setColorAt(0.5, QColor("#9A5BD6"))
    g.setColorAt(1, QColor("#F29A6B"))
    p.fillRect(pm.rect(), g)
    blobs = [("#FFD166", 0.25, 0.3), ("#06D6A0", 0.75, 0.2), ("#EF476F", 0.55, 0.8), ("#118AB2", 0.15, 0.85)]
    for i, (color, fx, fy) in enumerate(blobs):
        c = QPointF(w * ((fx + seed * 0.13 * (i + 1)) % 1), h * fy)
        rg = QRadialGradient(c, w * 0.28)
        col = QColor(color)
        rg.setColorAt(0, col)
        col.setAlpha(0)
        rg.setColorAt(1, col)
        p.setBrush(rg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c, w * 0.28, w * 0.28)
    # окно документа под островом
    doc = QRectF(w * 0.12, 60, w * 0.76, h - 40)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawRoundedRect(doc, 10, 10)
    p.setBrush(QColor(30, 30, 40, 200))
    for k in range(18):
        y = doc.top() + 30 + k * 26
        p.drawRoundedRect(QRectF(doc.left() + 30, y, doc.width() * (0.55 + 0.35 * math.sin(k * 1.7) ** 2), 9), 4, 4)
    p.end()
    return pm


def plain_page(w: int, h: int, dark: bool) -> QPixmap:
    """Тёмный редактор кода или светлая веб-страница — проверка читаемости на обычном фоне."""
    pm = QPixmap(w, h)
    pm.fill(QColor("#1E1E22") if dark else QColor("#F4F5F7"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    colors = ["#569CD6", "#C586C0", "#CE9178", "#9CDCFE", "#6A9955"] if dark else ["#1F2328", "#57606A", "#0969DA"]
    for k in range(22):
        y = 24 + k * 24
        x = 40 + (k % 4) * 28 if dark else 60
        width = (w - 2 * x) * (0.35 + 0.55 * math.sin(k * 1.3) ** 2)
        p.setBrush(QColor(colors[k % len(colors)]))
        p.drawRoundedRect(QRectF(x, y, width, 9), 4, 4)
    if not dark:
        p.setBrush(QColor("#0969DA"))
        p.drawRoundedRect(QRectF(60, 300, 220, 120), 12, 12)
    p.end()
    return pm


def make_island(theme, tile_w: int, seed: int, text: str = SAMPLE) -> Island:
    island = Island(FakeEngine(), theme)
    island.move((tile_w - island.width()) // 2, -16)
    island._status = "listening"
    island._phase = 1.3 + seed
    island._levels = deque(
        [max(0.03, 0.5 + 0.45 * math.sin(i * 0.55) * math.sin(i * 0.21 + 1)) for i in range(Island.BARS)],
        maxlen=Island.BARS,
    )
    island.set_text(text)
    island._h = float(island._target_height())
    island._appear = 1.0
    return island


def draw_label(p: QPainter, caption: str, tile_h: int) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(0, 0, 0, 150))
    p.setPen(Qt.PenStyle.NoPen)
    box = QRectF(16, tile_h - 50, 240, 34)
    p.drawRoundedRect(box, 17, 17)
    f = QFont("Segoe UI Variable Display")
    f.setPointSizeF(13)
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, caption)


GLOW_W, GLOW_H = 640, 230


def render_glow_tile(glow: str, bg: QPixmap, seed: int) -> QImage:
    """Только остров в «Тёмном стекле» с заданным цветом свечения."""
    theme = with_glow(THEMES["dark_glass"], glow)
    Backdrop.set(blur(bg, theme.blur), QPoint(0, 0))
    island = make_island(theme, GLOW_W, seed, "Так выглядит это свечение. Говорите — текст появляется прямо здесь.")
    img = QImage(GLOW_W, GLOW_H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.drawPixmap(0, 0, bg)
    p.drawPixmap(island.pos(), island.grab())
    draw_label(p, GLOWS[glow][0], GLOW_H)
    p.end()
    return img


def render_tile(theme_key: str, seed: int, bg: QPixmap | None = None, label: str | None = None) -> QImage:
    theme = THEMES[theme_key]
    bg = bg or wallpaper(TILE_W, TILE_H, seed)
    Backdrop.set(blur(bg, theme.blur), QPoint(0, 0))

    island = make_island(theme, TILE_W, seed)

    editor = Editor(FakeEngine(), theme=theme)
    editor.edit.setPlainText(SAMPLE)
    final = editor._final_geometry()
    editor.setGeometry(QRect(QPoint((TILE_W - final.width()) // 2, 190), final.size()))
    editor._layout_content(final.size())
    editor.content.layout().activate()
    editor._phase = 2.0 + seed

    img = QImage(TILE_W, TILE_H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.drawPixmap(0, 0, bg)
    p.drawPixmap(editor.pos(), editor.grab())
    p.drawPixmap(island.pos(), island.grab())
    draw_label(p, label or f"{list(THEMES).index(theme_key) + 1}. {theme.title}", TILE_H)
    p.end()
    return img


def save_sheet(tiles: list[QImage], cols: int, name: str) -> None:
    rows = math.ceil(len(tiles) / cols)
    gap = 16
    tw, th = tiles[0].width(), tiles[0].height()
    sheet = QImage(cols * tw + (cols + 1) * gap, rows * th + (rows + 1) * gap, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#111114"))
    p = QPainter(sheet)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        p.drawImage(gap + c * (tw + gap), gap + r * (th + gap), tile)
    p.end()
    out = ROOT / "docs" / name
    out.parent.mkdir(exist_ok=True)
    sheet.save(str(out))
    print(out)


def main() -> None:
    if sys.argv[1:] == ["--glows"]:
        # Каждый цвет свечения на тёмном фоне и на ярких обоях.
        tiles = []
        for i, glow in enumerate(GLOWS):
            tiles.append(render_glow_tile(glow, plain_page(GLOW_W, GLOW_H, dark=True), i))
            tiles.append(render_glow_tile(glow, wallpaper(GLOW_W, GLOW_H, i), i))
        save_sheet(tiles, 2, "glows.png")
        return
    if len(sys.argv) > 1:
        key = sys.argv[1]
        backgrounds = [
            ("Яркие обои", wallpaper(TILE_W, TILE_H, 0)),
            ("Тёмный фон", plain_page(TILE_W, TILE_H, dark=True)),
            ("Светлый фон", plain_page(TILE_W, TILE_H, dark=False)),
        ]
        tiles = [render_tile(key, i, bg, label) for i, (label, bg) in enumerate(backgrounds)]
        save_sheet(tiles, len(tiles), f"{key}.png")
        return
    keys = list(THEMES)
    save_sheet([render_tile(key, i) for i, key in enumerate(keys)], 3 if len(keys) > 4 else 2, "styles.png")


if __name__ == "__main__":
    main()
