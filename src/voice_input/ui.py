"""Окна в духе Apple: «остров» записи сверху экрана и карточка-редактор."""

from __future__ import annotations

import math
import time
from collections import deque

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import win32
from .themes import THEMES, Backdrop, Theme, alpha, css, get_theme, paint_card, siri_color, siri_gradient

TEXT_FONT = "Segoe UI Variable Text"
DISPLAY_FONT = "Segoe UI Variable Display"
ICON_FONT = "Segoe Fluent Icons"
MIC_GLYPH = "\ue720"  # Segoe Fluent Icons: Microphone
SEND_GLYPH = "\ue724"  # Send

WHITE = QColor(255, 255, 255)
BLUE = QColor(10, 132, 255)
GRAY = QColor(120, 120, 128)

SHADOW = 28  # поле под тень вокруг карточки
TOP_GAP = 12  # отступ острова от верха экрана


def font(size: float, weight: QFont.Weight = QFont.Weight.Normal, family: str = TEXT_FONT) -> QFont:
    f = QFont(family)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def screen_geometry(hwnd: int = 0) -> QRect:
    """Рабочая область монитора, где находится окно hwnd (иначе — где мышь)."""
    hmon = win32.monitor_of(hwnd)
    if hmon:
        for screen in QGuiApplication.screens():
            try:
                if int(screen.nativeInterface().handle()) == hmon:
                    return screen.availableGeometry()
            except Exception:
                break
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def backdrop_area(hwnd: int = 0) -> QRect:
    """Область сверху экрана, которую занимают остров и редактор, — её снимок нужен «стеклу»."""
    geo = screen_geometry(hwnd)
    w, h = 760, min(760, geo.height())
    return QRect(geo.center().x() - w // 2, geo.top(), w, h)


def wrap_text(text: str, f: QFont, width: float) -> tuple[QTextLayout, float]:
    """Раскладка текста по строкам. Ею же и рисуем — иначе замер и отрисовка расходятся."""
    layout = QTextLayout(text, f)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    y = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        line.setPosition(QPointF(0, y))
        y += line.height()
    layout.endLayout()
    return layout, y


TAIL_CHARS = 1500  # в остров влезает пара сотен символов — длинную диктовку целиком не раскладываем


def wrap_tail(text: str, f: QFont, width: float, max_lines: int) -> tuple[QTextLayout, float]:
    """Последние max_lines строк текста; обрезанное начало заменяется «…»."""
    if len(text) > TAIL_CHARS:
        text = "…" + text[-TAIL_CHARS:].split(" ", 1)[-1]
    layout, h = wrap_text(text, f, width)
    skip = layout.lineCount() - max_lines
    if skip <= 0:
        return layout, h
    # Начала строк берём из раскладки исходного текста — индексы относятся именно к нему.
    # «…» может добавить строку — тогда отрезаем ещё по одной; перебор конечен.
    starts = [layout.lineAt(i).textStart() for i in range(skip, layout.lineCount())]
    for start in starts:
        layout, h = wrap_text("…" + text[start:].lstrip(" …"), f, width)
        if layout.lineCount() <= max_lines:
            break
    return layout, h


def glyph_icon(glyph: str, color: QColor, size: int = 32) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(color)
    p.setFont(font(size * 0.42, family=ICON_FONT))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return QIcon(pm)


def make_tray_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BLUE)
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(WHITE)
    p.setFont(font(26, family=ICON_FONT))
    p.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, MIC_GLYPH)
    p.end()
    return QIcon(pm)


# =============================================================================== остров записи


class Island(QWidget):
    """Плашка сверху экрана: микрофон, живая волна, таймер и распознанный текст."""

    WIDTH = 480
    HEADER = 52
    PAD = 20
    MAX_LINES = 4
    BARS = 30

    DEMO_TEXT = "Так выглядит стиль «{}». Говорите — текст появляется прямо здесь."

    def __init__(self, engine, theme: Theme | None = None):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.engine = engine
        self.theme = theme or get_theme(None)
        self._demo = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.text_font = font(11.5)
        self.hint_font = font(8.6)
        self.timer_font = font(9.5, QFont.Weight.DemiBold)
        self.label_font = font(9.5, QFont.Weight.DemiBold)
        self.hint_fm = QFontMetrics(self.hint_font)

        max_text = QFontMetrics(self.text_font).lineSpacing() * (self.MAX_LINES + 1)
        self._max_h = self.HEADER + max_text + 60
        self.setFixedSize(self.WIDTH + 2 * SHADOW, self._max_h + 2 * SHADOW)

        self._text = ""
        self._status = "listening"  # listening | loading (модель) | starting (микрофон) | processing
        self._armed = ""  # "stop" | "send": команда сказана, ждём секунду тишины
        self._body: QTextLayout | None = None
        self._body_h = 0.0
        self._t0 = time.monotonic()
        self._levels: deque[float] = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._last_sample = 0.0
        self._phase = 0.0
        self._leaving = False

        self._relayout()
        self._h = float(self._target_height())
        self._appear = 0.0
        self._h_anim = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve.Type.OutCubic)
        self._h_anim.valueChanged.connect(self._set_h)
        self._appear_anim = QVariantAnimation(self)
        self._appear_anim.valueChanged.connect(self._set_appear)
        self._appear_anim.finished.connect(self._appear_finished)

        # 30 кадров в секунду для волны; анимации появления/роста сами рисуют чаще.
        self._tick = QTimer(self, interval=33)
        self._tick.timeout.connect(self._on_tick)
        self._demo_timer = QTimer(self, interval=4000, singleShot=True)
        self._demo_timer.timeout.connect(lambda: self._demo and self.dismiss())

    # ----------------------------------------------------------------- API

    def present(self, loading: bool, hwnd: int = 0, demo: bool = False, status: str | None = None) -> None:
        self._demo = demo
        self._text = self.DEMO_TEXT.format(self.theme.title) if demo else ""
        self._status = status or ("loading" if loading else "listening")
        self._armed = ""
        self._relayout()
        self._t0 = time.monotonic()
        self._levels = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._h = float(self._target_height())
        self._leaving = False

        geo = screen_geometry(hwnd)
        self.move(geo.center().x() - self.width() // 2, geo.top() + TOP_GAP - SHADOW)
        self.show()
        hwnd = int(self.winId())
        win32.add_ex_style(
            hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TOOLWINDOW | win32.WS_EX_TRANSPARENT | win32.WS_EX_LAYERED
        )
        self._tick.start()
        self._animate_appear(1.0, 420, QEasingCurve.Type.OutBack)
        if demo:
            self._demo_timer.start()  # перезапуск: при быстром переключении стилей демо не закроется раньше времени
        else:
            self._demo_timer.stop()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    @property
    def status(self) -> str:
        return self._status

    def level(self) -> float:
        if self._status != "listening":
            return 0.0
        if self._demo:
            t = self._phase
            return max(0.0, 0.55 + 0.35 * math.sin(t * 5.3) * math.sin(t * 1.7 + 1))
        return self.engine.level

    def dismiss(self, animated: bool = True) -> None:
        if not self.isVisible():
            return
        if not animated:
            self._finish_hide()
            return
        self._leaving = True
        self._animate_appear(0.0, 220, QEasingCurve.Type.InCubic)

    def set_text(self, text: str) -> None:
        self._text = text
        self._relayout()
        self._animate_height()

    def set_status(self, status: str) -> None:
        self._status = status
        self._relayout()
        self._animate_height()

    def set_armed(self, command: str) -> None:
        self._armed = command
        self.update()

    def card_rect_global(self) -> QRect:
        r = self._card_rect().toRect()
        return QRect(self.mapToGlobal(r.topLeft()), r.size())

    # ----------------------------------------------------------------- анимации

    def _target_height(self) -> int:
        h = self.HEADER
        if self._body is not None:
            h += math.ceil(self._body_h) + 6
        h += self.hint_fm.height() + 16
        return min(h, self._max_h)

    def _animate_height(self) -> None:
        target = float(self._target_height())
        if abs(target - self._h) < 0.5:
            return
        self._h_anim.stop()
        self._h_anim.setStartValue(self._h)
        self._h_anim.setEndValue(target)
        self._h_anim.start()

    def _set_h(self, v) -> None:
        self._h = float(v)
        self.update()

    def _animate_appear(self, end: float, ms: int, curve: QEasingCurve.Type) -> None:
        self._appear_anim.stop()
        self._appear_anim.setDuration(ms)
        self._appear_anim.setEasingCurve(curve)
        self._appear_anim.setStartValue(self._appear)
        self._appear_anim.setEndValue(end)
        self._appear_anim.start()

    def _set_appear(self, v) -> None:
        self._appear = float(v)
        self.update()

    def _appear_finished(self) -> None:
        if self._leaving:
            self._finish_hide()

    def _finish_hide(self) -> None:
        self._appear_anim.stop()
        self._tick.stop()
        self._appear = 0.0
        self._leaving = False
        self.hide()

    def _on_tick(self) -> None:
        now = time.monotonic()
        self._phase += 0.033
        if now - self._last_sample >= 0.045:
            self._last_sample = now
            self._levels.append(self.level())
        self.update()

    # ----------------------------------------------------------------- отрисовка

    def _relayout(self) -> None:
        text = self._text
        if self._status == "loading" and not text:
            text = "Загружаю модель распознавания…"
        if text:
            self._body, self._body_h = wrap_tail(text, self.text_font, self.WIDTH - 2 * self.PAD, self.MAX_LINES)
        else:
            self._body, self._body_h = None, 0.0

    def _card_rect(self) -> QRectF:
        return QRectF(SHADOW, SHADOW, self.WIDTH, self._h)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        a = self._appear
        opacity = max(0.0, min(1.0, a * 1.6))
        if opacity <= 0.001:
            return
        card = self._card_rect()
        # Выезд сверху с лёгким «пружинным» масштабом, как у Dynamic Island.
        scale = 0.6 + 0.4 * a
        cx, top = card.center().x(), card.top()
        p.translate(cx, top)
        p.scale(scale, 0.35 + 0.65 * a)
        p.translate(-cx, -top + (1 - a) * -26)
        p.setOpacity(opacity)

        radius = min(26.0, self._h / 2)
        t = self.theme
        paint_card(p, card, radius, t, widget_origin=self.mapToGlobal(QPoint(0, 0)), phase=self._phase, level=self.level())

        x0, y0 = card.left(), card.top()
        mid_y = y0 + self.HEADER / 2 + 2
        self._paint_orb(p, QPointF(x0 + self.PAD + 14, mid_y))

        p.setFont(self.label_font)
        p.setPen(t.text)
        label = {"loading": "Подготовка", "starting": "Подготовка", "processing": "Обработка"}.get(self._status, "Слушаю")
        if self._status == "listening" and self._armed:
            label = {"send": "Отправлю…", "stop": "Готово…"}[self._armed]
        label_w = p.fontMetrics().horizontalAdvance(label)
        p.drawText(QRectF(x0 + self.PAD + 38, y0, label_w + 4, self.HEADER + 4), Qt.AlignmentFlag.AlignVCenter, label)

        wave_x = max(150, self.PAD + 38 + label_w + 14)  # длинная подпись («Отправлю…») сдвигает волну
        self._paint_wave(p, QRectF(x0 + wave_x, mid_y - 14, self.WIDTH - wave_x - 76, 28))

        elapsed = int(time.monotonic() - self._t0)
        p.setFont(self.timer_font)
        p.setPen(t.muted)
        p.drawText(
            QRectF(x0, y0, self.WIDTH - self.PAD, self.HEADER + 4),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{elapsed // 60}:{elapsed % 60:02d}",
        )

        y = y0 + self.HEADER
        clip_bottom = card.bottom() - self.hint_fm.height() - 14
        if self._body is not None:
            p.save()
            p.setClipRect(QRectF(x0, y, self.WIDTH, max(0.0, clip_bottom - y)))
            p.setPen(t.muted if self._status == "loading" and not self._text else t.text)
            self._body.draw(p, QPointF(x0 + self.PAD, y))
            p.restore()

        p.setFont(self.hint_font)
        p.setPen(alpha(t.muted, t.muted.alphaF() * 0.9))
        if self._armed:
            hint = "Продолжите говорить, чтобы не останавливаться   ·   Esc — отмена"
        else:
            hint = "«стоп» — в редактор   ·   «отправить» — сразу   ·   Esc — отмена"
        p.drawText(
            QRectF(x0 + self.PAD, card.bottom() - self.hint_fm.height() - 12, self.WIDTH - 2 * self.PAD, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            hint,
        )

    def _paint_orb(self, p: QPainter, c: QPointF) -> None:
        r = 14.0
        t = self.theme
        listening = self._status == "listening"
        level = self.level()
        base = t.orb if listening else GRAY
        if t.rainbow and listening:
            base = siri_color(self._phase * 0.2)
        if listening:
            pulse = r + 3 + level * 9
            glow = QRadialGradient(c, pulse + 6)
            glow.setColorAt(0.0, alpha(base, 0.45))
            glow.setColorAt(1.0, alpha(base, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, pulse + 6, pulse + 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(siri_gradient(c, self._phase * 3) if t.rainbow and listening else base)
        p.drawEllipse(c, r, r)
        p.setPen(WHITE)
        p.setFont(font(10.5, family=ICON_FONT))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, MIC_GLYPH)
        if self._status in ("loading", "starting"):
            # крутящаяся дуга вокруг кружка
            pen = QPen(alpha(t.text, 0.8), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = int(-self._phase * 360 * 16 * 1.2) % (360 * 16)
            p.drawArc(QRectF(c.x() - r - 4, c.y() - r - 4, 2 * r + 8, 2 * r + 8), start, 90 * 16)

    def _paint_wave(self, p: QPainter, rect: QRectF) -> None:
        n = self.BARS
        gap = rect.width() / n
        bar_w = max(2.4, gap * 0.48)
        p.setPen(Qt.PenStyle.NoPen)
        levels = list(self._levels)
        t = self.theme
        for i in range(n):
            x = rect.left() + i * gap + (gap - bar_w) / 2
            if self._status == "listening":
                v = levels[i]
                # лёгкая «живость» даже в тишине
                v = max(v, 0.04 + 0.03 * math.sin(self._phase * 6 + i * 0.7))
                fresh = 0.35 + 0.65 * (i / (n - 1))
                base = siri_color(i / n * 0.8 + self._phase * 0.15) if t.rainbow else t.wave
                color = alpha(base, 0.25 + 0.7 * fresh * min(1.0, 0.4 + v))
            else:
                # бегущая волна, пока ждём модель или финальный текст
                v = 0.08 + 0.18 * (0.5 + 0.5 * math.sin(self._phase * 7 - i * 0.45))
                color = alpha(t.wave, 0.35)
            h = max(bar_w, rect.height() * min(1.0, v))
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, rect.center().y() - h / 2, bar_w, h), bar_w / 2, bar_w / 2)


# =============================================================================== редактор


class _TextEdit(QPlainTextEdit):
    submit = Signal(bool)  # True — вставить и отправить
    escape = Signal()

    def __init__(self, enter_confirms: bool):
        super().__init__()
        self.enter_confirms = enter_confirms

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = e.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                self.submit.emit(True)
                return
            if self.enter_confirms and not mods & Qt.KeyboardModifier.ShiftModifier:
                self.submit.emit(False)
                return
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
                return
        super().keyPressEvent(e)


class _ListeningBadge(QWidget):
    """Пульсирующая точка + подсказка голосовых команд."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.listening = True
        self.theme = get_theme(None)
        self._f = font(8.8, QFont.Weight.DemiBold)
        self._text = "Скажите «ок» или «отправить»"
        fm = QFontMetrics(self._f)
        self.setFixedSize(fm.horizontalAdvance(self._text) + 26, 22)
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self.update)

    def showEvent(self, e) -> None:
        self._timer.start()

    def hideEvent(self, e) -> None:
        self._timer.stop()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(8, self.height() / 2)
        level = self.engine.level if self.listening else 0.0
        color = self.theme.orb if self.listening else GRAY
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(alpha(color, 0.25))
        p.drawEllipse(c, 4 + level * 5, 4 + level * 5)
        p.setBrush(color)
        p.drawEllipse(c, 3.6, 3.6)
        p.setFont(self._f)
        p.setPen(self.theme.muted)
        p.drawText(QRectF(20, 0, self.width() - 20, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)


BUTTON_CSS = """
QPushButton {{
    font-family: '{family}'; font-size: 10pt; font-weight: 600;
    border: none; border-radius: 15px; padding: 0 18px; min-height: 30px;
    color: {fg}; background: {bg};
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {pressed}; }}
"""


class Editor(QWidget):
    """Карточка для правки распознанного текста перед вставкой."""

    confirmed = Signal(str, bool)  # текст, отправить ли сразу
    cancelled = Signal()

    WIDTH = 620
    PAD = 20

    def __init__(self, engine, enter_confirms: bool = True, theme: Theme | None = None):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.engine = engine
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._closing = False
        self._hwnd = 0
        self._phase = 0.0
        # Подсветка дышит вместе с голосом (а радуга ещё и переливается) — перерисовываем 25 раз в секунду.
        self._glow_timer = QTimer(self, interval=40)
        self._glow_timer.timeout.connect(self._glow_tick)

        self.content = QWidget(self)
        self._fx = QGraphicsOpacityEffect(self.content)
        self._fx.setOpacity(1.0)
        self.content.setGraphicsEffect(self._fx)

        self.title = title = QLabel("Проверьте текст")
        title.setFont(font(10, QFont.Weight.DemiBold))
        self.badge = _ListeningBadge(engine)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.badge)

        self.edit = _TextEdit(enter_confirms)
        self.edit.setFont(font(12))
        self.edit.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.document().setDocumentMargin(2)
        self.edit.textChanged.connect(self._fit_height)
        self.edit.submit.connect(lambda send: self.confirm(send))
        self.edit.escape.connect(self.cancel)

        self.hint = hint = QLabel("Enter — вставить   ·   Ctrl+Enter — отправить")
        hint.setFont(font(8.4))

        self.cancel_btn = QPushButton("Отмена")
        self.ok_btn = QPushButton("ОК")
        self.send_btn = QPushButton(" Отправить")
        for b in (self.cancel_btn, self.ok_btn, self.send_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_btn.clicked.connect(self.cancel)
        self.ok_btn.clicked.connect(lambda: self.confirm(False))
        self.send_btn.clicked.connect(lambda: self.confirm(True))

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(hint)
        footer.addStretch(1)
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.ok_btn)
        footer.addWidget(self.send_btn)

        lay = QVBoxLayout(self.content)
        lay.setContentsMargins(self.PAD, 16, self.PAD, 16)
        lay.setSpacing(12)
        lay.addLayout(header)
        lay.addWidget(self.edit)
        lay.addLayout(footer)

        self._geo_anim = QPropertyAnimation(self, b"geometry", self, duration=300)
        self._geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self, duration=160)
        self._geo_anim.finished.connect(self._fade_in_content)
        self.apply_theme(theme or get_theme(None))

    # ----------------------------------------------------------------- API

    def apply_theme(self, t: Theme) -> None:
        self.theme = t
        self.badge.theme = t
        self.title.setStyleSheet(f"color: {css(t.text)}; background: transparent;")
        self.hint.setStyleSheet(f"color: {css(t.muted, t.muted.alphaF() * 0.85)}; background: transparent;")
        self.edit.setStyleSheet(
            f"""
            QPlainTextEdit {{ background: {css(t.field)}; color: {css(t.text)};
                             border-radius: 12px; padding: 8px 10px;
                             selection-background-color: {css(t.accent, 0.45)}; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 6px 2px; }}
            QScrollBar::handle:vertical {{ background: {css(t.text, 0.22)}; border-radius: 2px; min-height: 24px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            """
        )
        neutral = WHITE if t.dark else QColor(0, 0, 0)
        self.cancel_btn.setStyleSheet(
            BUTTON_CSS.format(
                family=TEXT_FONT,
                fg=css(t.text),
                bg=css(neutral, 0.10 if t.dark else 0.06),
                hover=css(neutral, 0.16 if t.dark else 0.10),
                pressed=css(neutral, 0.07 if t.dark else 0.04),
            )
        )
        if t.button is not None:
            # Свой цвет кнопок: «ОК» и «Отправить» одинаковые.
            solid = BUTTON_CSS.format(
                family=TEXT_FONT,
                fg=css(t.button_text),
                bg=css(t.button),
                hover=css(t.button, min(1.0, t.button.alphaF() + 0.06)),
                pressed=css(t.button, t.button.alphaF() * 0.8),
            )
            self.ok_btn.setStyleSheet(solid)
            self.send_btn.setStyleSheet(solid)
            self.send_btn.setIcon(glyph_icon(SEND_GLYPH, t.button_text))
            self.update()
            return
        self.ok_btn.setStyleSheet(
            BUTTON_CSS.format(
                family=TEXT_FONT,
                fg="white",
                bg=css(t.accent),
                hover=css(t.accent.lighter(115)),
                pressed=css(t.accent.darker(115)),
            )
        )
        send_fg = t.accent.lighter(135) if t.dark else t.accent
        send_bg = 0.16
        if t.glass and t.dark:
            # на стекле под кнопкой может оказаться что угодно — делаем её плотнее
            send_fg, send_bg = WHITE, 0.4
        self.send_btn.setIcon(glyph_icon(SEND_GLYPH, send_fg))
        self.send_btn.setStyleSheet(
            BUTTON_CSS.format(
                family=TEXT_FONT,
                fg=css(send_fg),
                bg=css(t.accent, send_bg),
                hover=css(t.accent, send_bg + 0.1),
                pressed=css(t.accent, send_bg - 0.04),
            )
        )
        self.update()

    def open(self, text: str, from_rect: QRect | None = None, hwnd: int = 0) -> None:
        self._closing = False
        self._hwnd = hwnd
        self.badge.listening = True
        self.edit.setPlainText(text)
        cursor = self.edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.edit.setTextCursor(cursor)

        final = self._final_geometry()
        if from_rect is not None:
            start = from_rect.adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW)
            self._fx.setOpacity(0.0)
            self.setGeometry(start)
            self._layout_content(final.size())
            self.show()
            self._geo_anim.stop()
            self._geo_anim.setStartValue(start)
            self._geo_anim.setEndValue(final)
            self._geo_anim.start()
        else:
            self.setGeometry(final)
            self._layout_content(final.size())
            self._fx.setOpacity(1.0)
            self.show()
        if self.theme.glow:
            self._glow_timer.start()
        self._take_focus()

    def set_listening(self, on: bool) -> None:
        self.badge.listening = on

    def hideEvent(self, e) -> None:
        self._glow_timer.stop()
        super().hideEvent(e)

    # Сначала сигнал, потом hide(): обработчик возвращает фокус исходному окну, пока редактор ещё
    # активен. Активному процессу Windows разрешает отдать фокус, а спрятанному — уже не всегда.
    def confirm(self, send: bool = False) -> None:
        if self.isVisible() and not self._closing:
            self._closing = True
            text = self.edit.toPlainText().strip()
            self.confirmed.emit(text, send)
            self.hide()

    def cancel(self) -> None:
        if self.isVisible() and not self._closing:
            self._closing = True
            self.cancelled.emit()
            self.hide()

    # ----------------------------------------------------------------- внутреннее

    def _take_focus(self) -> None:
        win32.force_foreground(int(self.winId()))
        self.raise_()
        self.activateWindow()
        self.edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _edit_height(self) -> int:
        fm = QFontMetrics(self.edit.font())
        doc_h = self.edit.document().size().height() * fm.lineSpacing()  # size() в строках для QPlainTextEdit
        lines_min, lines_max = 3, 12
        h = max(fm.lineSpacing() * lines_min, min(doc_h, fm.lineSpacing() * lines_max))
        return int(h) + 22

    def _final_geometry(self) -> QRect:
        self.edit.setFixedWidth(self.WIDTH - 2 * self.PAD)
        self.edit.setFixedHeight(self._edit_height())
        content_h = self.content.layout().sizeHint().height()
        w, h = self.WIDTH + 2 * SHADOW, content_h + 2 * SHADOW
        geo = screen_geometry(self._hwnd)
        return QRect(geo.center().x() - w // 2, geo.top() + TOP_GAP - SHADOW, w, h)

    def _layout_content(self, size) -> None:
        self.content.setGeometry(SHADOW, SHADOW, size.width() - 2 * SHADOW, size.height() - 2 * SHADOW)

    def _fit_height(self) -> None:
        if not self.isVisible() or self._geo_anim.state() == QPropertyAnimation.State.Running:
            return
        new_h = self._edit_height()
        if new_h == self.edit.height():
            return
        final = self._final_geometry()
        final.moveTopLeft(self.geometry().topLeft())
        self.setGeometry(final)
        self._layout_content(final.size())

    def _fade_in_content(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._take_focus()

    def _glow_tick(self) -> None:
        self._phase += 0.04
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        paint_card(
            p,
            card,
            min(24.0, card.height() / 2),
            self.theme,
            widget_origin=self.mapToGlobal(QPoint(0, 0)),
            phase=self._phase,
            level=self.engine.level * 0.6,
        )

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.cancel()
            return
        super().keyPressEvent(e)

