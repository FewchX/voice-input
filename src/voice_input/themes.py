"""Стили окон: цвета, формы и эффекты (подсветка Siri, матовое стекло)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap

# Палитра подсветки Apple Intelligence / Siri.
SIRI = [QColor("#FF8A3D"), QColor("#FF2D95"), QColor("#B44DFF"), QColor("#3D9BFF"), QColor("#2EE6D6"), QColor("#FF8A3D")]


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    dark: bool
    card: QColor  # фон карточки (для стекла — оттенок поверх размытого фона)
    border: QColor
    text: QColor
    muted: QColor  # подсказки, таймер
    accent: QColor  # кнопка «ОК», выделение текста
    orb: QColor  # кружок микрофона и точка «слушаю»
    wave: QColor  # столбики волны
    field: QColor  # фон поля редактора
    shadow: float = 1.0
    glow: bool = False  # подсветка по краю, реагирует на голос
    glow_color: QColor | None = None  # цвет подсветки; None — переливающаяся радуга Siri
    glass: bool = False  # размытый снимок экрана под карточкой
    blur: int = 5  # сила размытия стекла: каждый шаг уменьшает снимок вдвое
    button: QColor | None = None  # фон кнопок «ОК» и «Отправить»; None — цвет accent
    button_text: QColor = field(default_factory=lambda: QColor("#FFFFFF"))

    @property
    def rainbow(self) -> bool:
        return self.glow and self.glow_color is None


def _c(hex_: str, a: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(a)
    return c


THEMES: dict[str, Theme] = {
    t.key: t
    for t in (
        Theme(
            key="island",
            title="Остров",
            dark=True,
            card=_c("#1C1C1E", 0.965),
            border=_c("#FFFFFF", 0.09),
            text=_c("#FFFFFF", 0.94),
            muted=_c("#FFFFFF", 0.42),
            accent=_c("#0A84FF"),
            orb=_c("#FF453A"),
            wave=_c("#FFFFFF"),
            field=_c("#FFFFFF", 0.06),
        ),
        Theme(
            key="siri",
            title="Siri",
            dark=True,
            card=_c("#0D0D12", 0.95),
            border=_c("#FFFFFF", 0.06),
            text=_c("#FFFFFF", 0.95),
            muted=_c("#FFFFFF", 0.42),
            accent=_c("#7A5CFF"),
            orb=_c("#FF2D95"),
            wave=_c("#FFFFFF"),
            field=_c("#FFFFFF", 0.05),
            shadow=0.7,
            glow=True,
        ),
        Theme(
            key="dark_glass",
            title="Тёмное стекло",
            dark=True,
            card=_c("#0A0C12", 0.52),
            border=_c("#FFFFFF", 0.08),
            text=_c("#FFFFFF", 0.97),
            muted=_c("#FFFFFF", 0.7),
            accent=_c("#0A84FF"),
            orb=_c("#FF3B30"),
            wave=_c("#FFFFFF"),
            field=_c("#000000", 0.25),
            shadow=0.8,
            glow=True,
            glow_color=_c("#FFFFFF"),
            glass=True,
            blur=3,
            button=_c("#FFFFFF", 0.94),
            button_text=_c("#111114"),
        ),
        Theme(
            key="glass",
            title="Стекло",
            dark=False,
            card=_c("#FFFFFF", 0.52),
            border=_c("#FFFFFF", 0.75),
            text=_c("#111114", 0.92),
            muted=_c("#111114", 0.5),
            accent=_c("#007AFF"),
            orb=_c("#FF3B30"),
            wave=_c("#111114"),
            field=_c("#FFFFFF", 0.45),
            shadow=0.6,
            glass=True,
        ),
        Theme(
            key="light",
            title="Светлая",
            dark=False,
            card=_c("#FBFBFD", 0.985),
            border=_c("#000000", 0.08),
            text=_c("#1D1D1F", 0.95),
            muted=_c("#3C3C43", 0.55),
            accent=_c("#007AFF"),
            orb=_c("#FF3B30"),
            wave=_c("#1D1D1F"),
            field=_c("#767680", 0.10),
            shadow=0.8,
        ),
    )
}
DEFAULT_THEME = "dark_glass"


def get_theme(key: str | None) -> Theme:
    return THEMES.get(key or "", THEMES[DEFAULT_THEME])


# Цвета подсветки для меню трея; None — радуга Siri.
GLOWS: dict[str, tuple[str, str | None]] = {
    "white": ("Белое", "#FFFFFF"),
    "orange": ("Оранжевое", "#FF8A1F"),
    "blue": ("Синее", "#2E8BFF"),
    "purple": ("Фиолетовое", "#A066FF"),
    "pink": ("Розовое", "#FF4FA3"),
    "green": ("Зелёное", "#30D158"),
    "rainbow": ("Радуга", None),
}


def with_glow(theme: Theme, glow: str | None) -> Theme:
    """Стиль с выбранным цветом подсветки: ключ из GLOWS или «#RRGGBB»; None — как задумано в стиле."""
    if not theme.glow or not glow:
        return theme
    if glow in GLOWS:
        hex_ = GLOWS[glow][1]
        return replace(theme, glow_color=QColor(hex_) if hex_ else None)
    color = QColor(glow)
    return replace(theme, glow_color=color) if color.isValid() else theme


def glow_key(theme: Theme) -> str | None:
    """Какой пункт GLOWS соответствует подсветке стиля (для галочки в меню)."""
    if theme.rainbow:
        return "rainbow"
    for key, (_, hex_) in GLOWS.items():
        if hex_ and theme.glow_color is not None and QColor(hex_).rgb() == theme.glow_color.rgb():
            return key
    return None


def alpha(color: QColor, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def css(color: QColor, a: float | None = None) -> str:
    c = color if a is None else alpha(color, a)
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.3f})"


def siri_color(t: float) -> QColor:
    """Цвет палитры Siri в точке t (0..1, по кругу)."""
    t = t % 1.0
    n = len(SIRI) - 1
    i = min(int(t * n), n - 1)
    k = t * n - i
    a, b = SIRI[i], SIRI[i + 1]
    return QColor(
        int(a.red() + (b.red() - a.red()) * k),
        int(a.green() + (b.green() - a.green()) * k),
        int(a.blue() + (b.blue() - a.blue()) * k),
    )


def siri_gradient(center: QPointF, phase: float) -> QConicalGradient:
    g = QConicalGradient(center, (-phase * 70) % 360)
    for i, c in enumerate(SIRI):
        g.setColorAt(i / (len(SIRI) - 1), c)
    return g


def glow_brush(theme: Theme, center: QPointF, phase: float) -> QBrush:
    if theme.glow_color is None:
        return QBrush(siri_gradient(center, phase))
    return QBrush(theme.glow_color)


# --------------------------------------------------------------------------- размытый фон


class Backdrop:
    """Размытый снимок области экрана — основа для «стекла».

    Снимок делается один раз перед показом окна: живое размытие окна,
    перекрывающего другие, Windows для таких окон не даёт.
    """

    pixmap: QPixmap | None = None
    origin = QPoint()

    @classmethod
    def capture(cls, area: QRect, steps: int = 5) -> None:
        screen = QGuiApplication.screenAt(area.center()) or QGuiApplication.primaryScreen()
        geo = screen.geometry()
        area = area.intersected(geo)
        if area.isEmpty():
            cls.pixmap = None
            return
        shot = screen.grabWindow(0, area.x() - geo.x(), area.y() - geo.y(), area.width(), area.height())
        cls.set(blur(shot, steps), area.topLeft())

    @classmethod
    def set(cls, pixmap: QPixmap | None, origin: QPoint) -> None:
        cls.pixmap, cls.origin = pixmap, QPoint(origin)


def blur(pm: QPixmap, steps: int = 5) -> QPixmap:
    """Дешёвое гауссоподобное размытие: каскад уменьшений и плавное увеличение обратно."""
    if pm.isNull():
        return pm
    img = pm.toImage()
    dpr = pm.devicePixelRatio()
    w, h = img.width(), img.height()
    small = img
    for _ in range(steps):
        small = small.scaled(
            max(1, small.width() // 2), max(1, small.height() // 2), mode=Qt.TransformationMode.SmoothTransformation
        )
    for _ in range(steps):
        small = small.scaled(
            min(w, small.width() * 2), min(h, small.height() * 2), mode=Qt.TransformationMode.SmoothTransformation
        )
    out = QPixmap.fromImage(small.scaled(w, h, mode=Qt.TransformationMode.SmoothTransformation))
    out.setDevicePixelRatio(dpr)
    return out


# --------------------------------------------------------------------------- карточка


def card_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_card(
    p: QPainter,
    rect: QRectF,
    radius: float,
    theme: Theme,
    *,
    widget_origin: QPoint | None = None,
    phase: float = 0.0,
    level: float = 0.0,
) -> None:
    """Тень (или подсветка) + карточка с обводкой; для стекла — размытый фон под ней.

    widget_origin — глобальные координаты левого верхнего угла виджета (нужны стеклу).
    """
    base_opacity = p.opacity()
    path = card_path(rect, radius)

    # Тень.
    p.setPen(Qt.PenStyle.NoPen)
    steps = 14
    for i in range(steps, 0, -1):
        k = i / steps
        r = rect.adjusted(-i * 1.4, -i * 0.9, i * 1.4, i * 1.9)
        p.setBrush(QColor(0, 0, 0, int(26 * theme.shadow * (1 - k) ** 2)))
        p.drawRoundedRect(r, radius + i, radius + i)

    # Подсветка: мягкое свечение наружу, дышит вместе с голосом.
    # Радуга Siri яркая и переливается, одноцветная — неброская.
    if theme.glow:
        brush = glow_brush(theme, rect.center(), phase)
        k = 1.0 if theme.rainbow else 0.55
        for width, a in ((26, 0.07), (16, 0.13), (9, 0.24), (4, 0.45)):
            p.setOpacity(base_opacity * a * k * (0.75 + 0.9 * level))
            p.setPen(QPen(brush, width * (0.85 + 0.5 * level)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.setOpacity(base_opacity)

    # Стекло: непрозрачная подложка (на случай, если снимка нет) + размытый снимок.
    if theme.glass:
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(28, 28, 34, 240) if theme.dark else QColor(236, 236, 240, 235))
        p.drawRect(rect)
        if Backdrop.pixmap is not None and widget_origin is not None:
            p.drawPixmap(QPointF(Backdrop.origin - widget_origin), Backdrop.pixmap)
        p.restore()

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.card)
    p.drawPath(path)

    # Стекло с подсветкой: свет от края чуть затекает внутрь карточки.
    if theme.glow and theme.glass:
        p.save()
        p.setClipPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        brush = glow_brush(theme, rect.center(), phase)
        for width, a in ((28, 0.05), (12, 0.09)):
            p.setOpacity(base_opacity * a * (0.7 + 0.8 * level))
            p.setPen(QPen(brush, width))
            p.drawPath(path)
        p.restore()

    if theme.rainbow:
        pen = QPen(QBrush(siri_gradient(rect.center(), phase)), 1.6)
        p.setOpacity(base_opacity * 0.95)
    elif theme.glow:
        pen = QPen(alpha(theme.glow_color, 0.5 + 0.3 * level), 1.2)
    else:
        pen = QPen(theme.border, 1)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    if theme.glass:
        # тонкий тёмный контур снаружи белого блика — стекло читается на светлом фоне
        p.setPen(QPen(QColor(0, 0, 0, 28), 1))
        p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5)
    p.setOpacity(base_opacity)
