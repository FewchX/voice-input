"""Глобальный low-level хук мыши: быстрое «ЛКМ → ПКМ → ЛКМ» на одном месте запускает диктовку.

Первый левый клик проходит как обычно (им удобно поставить курсор в поле ввода).
Правый клик, пришедший вскоре после него рядом, придерживается: если за
`window_ms` следует ещё один левый — это жест, и оба клика проглатываются
(контекстное меню не появляется). Если нет — правый клик вбрасывается обратно
в ту же точку, так что обычная работа мышью не ломается.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from . import win32
from .hotkey import HOOKPROC, REINSTALL_EVERY_MS, WM_QUIT, WM_TIMER
from .win32 import kernel32, user32

log = logging.getLogger(__name__)

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C

# Флаги SendInput для повторного вброса событий.
_REPLAY_FLAGS = {
    WM_MOUSEMOVE: 0,
    WM_LBUTTONDOWN: 0x0002,
    WM_LBUTTONUP: 0x0004,
    WM_RBUTTONDOWN: 0x0008,
    WM_RBUTTONUP: 0x0010,
    WM_MBUTTONDOWN: 0x0020,
    WM_MBUTTONUP: 0x0040,
    WM_XBUTTONDOWN: 0x0080,
    WM_XBUTTONUP: 0x0100,
    WM_MOUSEWHEEL: 0x0800,
    WM_MOUSEHWHEEL: 0x1000,
}

RADIUS = 16  # px: все три клика — практически в одной точке


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", win32.ULONG_PTR),
    ]


@dataclass
class _Click:
    time: int
    x: int
    y: int
    flags: int = 0  # MSLLHOOKSTRUCT.flags — для лога (например, LLMHF_INJECTED)

    def near(self, x: int, y: int) -> bool:
        return abs(x - self.x) <= RADIUS and abs(y - self.y) <= RADIUS

    def within(self, t: int, ms: int) -> bool:
        return (t - self.time) & 0xFFFFFFFF <= ms


def _replay(msg: int, x: int, y: int, data: int = 0) -> win32.INPUT:
    if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
        data = ctypes.c_short(data).value  # прокрутка — знаковое число
    return win32.mouse_input(x, y, _REPLAY_FLAGS[msg], data)


class MouseGesture:
    """Колбэк вызывается из потока хука — внутри него нельзя трогать GUI напрямую."""

    def __init__(self, on_gesture: Callable[[], None], window_ms: int = 400):
        self.on_gesture = on_gesture
        self.window_ms = window_ms
        # Пока открыт наш редактор, клики в нём должны работать как обычно.
        self.enabled = True

        self._proc = HOOKPROC(self._hook_proc)
        self._hook = None
        self._thread_id = 0
        self._ready = threading.Event()

        self._left: _Click | None = None  # последний левый клик, отпущенный или нет
        self._left_up = False
        self._first: _Click | None = None  # левый клик, с которого начался текущий жест
        self._right: _Click | None = None  # придержанный правый клик
        self._right_up = False
        self._timer = 0
        self._swallow_up: set[int] = set()

    # -------------------------------------------------------------- жизненный цикл

    @property
    def running(self) -> bool:
        return bool(self._thread_id)

    def start(self) -> None:
        if self.running:
            return
        self._ready.clear()
        threading.Thread(target=self._run, name="mouse-hook", daemon=True).start()
        self._ready.wait(2)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _install(self) -> None:
        """Как и у клавиатуры: Windows молча снимает хук после таймаута — ставим заново."""
        new = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not new:
            log.error("SetWindowsHookExW(mouse) failed: %s", ctypes.get_last_error())
            return
        old, self._hook = self._hook, new
        if old:
            user32.UnhookWindowsHookEx(old)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._install()
        self._ready.set()
        log.info("жест мышью включён")
        watchdog = user32.SetTimer(None, 0, REINSTALL_EVERY_MS, None)
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message != WM_TIMER:
                    continue
                if msg.wParam == self._timer:
                    self._on_timeout()
                elif msg.wParam == watchdog and self._right is None:
                    self._install()
        finally:
            self._flush_right()
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._thread_id = 0

    # -------------------------------------------------------------- логика

    def _hook_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        # Перемещения идут сотнями в секунду — без придержанного клика пропускаем их сразу.
        if n_code == 0 and (w_param != WM_MOUSEMOVE or self._right is not None):
            ms = MSLLHOOKSTRUCT.from_address(l_param)
            if ms.dwExtraInfo != win32.INJECT_TAG:
                try:
                    if self._handle(w_param, ms.pt.x, ms.pt.y, ms.mouseData >> 16, ms.time, ms.flags):
                        return 1
                except Exception:  # исключение в хуке не должно ломать мышь в системе
                    log.exception("mouse hook error")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _handle(self, msg: int, x: int, y: int, data: int, t: int, flags: int = 0) -> bool:
        """True — событие проглатываем."""
        if msg in (WM_LBUTTONUP, WM_RBUTTONUP) and msg in self._swallow_up:
            self._swallow_up.discard(msg)
            return True

        if self._right is not None:
            return self._while_right_held(msg, x, y, data, t, flags)

        if msg == WM_LBUTTONDOWN:
            self._left, self._left_up = _Click(t, x, y, flags), False
        elif msg == WM_LBUTTONUP:
            self._left_up = self._left is not None
        elif msg == WM_RBUTTONDOWN:
            left, self._left = self._left, None
            if (
                self.enabled
                and left is not None
                and self._left_up
                and left.within(t, self.window_ms)
                and left.near(x, y)
                and not win32.modifiers_down()
            ):
                self._first = left
                self._right, self._right_up = _Click(t, x, y, flags), False
                self._timer = self._set_timer(self.window_ms)
                return True
        return False

    def _while_right_held(self, msg: int, x: int, y: int, data: int, t: int, flags: int = 0) -> bool:
        right = self._right
        if msg == WM_RBUTTONUP and not self._right_up:
            self._right_up = True
            return True
        if msg == WM_MOUSEMOVE and right.near(x, y):
            return False  # рука чуть дрогнула — это всё ещё жест
        # Левая может быть нажата и до того, как отпущена правая, — при быстром жесте так бывает.
        if msg == WM_LBUTTONDOWN and right.near(x, y) and right.within(t, self.window_ms):
            first = self._first
            log.info(
                "жест мыши: ЛКМ→ПКМ %d мс, ПКМ→ЛКМ %d мс, сдвиг %d/%d px, флаги %x/%x/%x",
                (right.time - first.time) & 0xFFFFFFFF,
                (t - right.time) & 0xFFFFFFFF,
                max(abs(right.x - first.x), abs(right.y - first.y)),
                max(abs(x - right.x), abs(y - right.y)),
                first.flags,
                right.flags,
                flags,
            )
            self._swallow_up.add(WM_LBUTTONUP)
            if not self._right_up:
                self._swallow_up.add(WM_RBUTTONUP)
            self._cancel_right()
            self.on_gesture()
            return True
        # Что-то другое: сначала отдаём придержанный правый клик, потом это событие — порядок сохраняется.
        self._flush_right(then=[_replay(msg, x, y, data)])
        return True

    def _on_timeout(self) -> None:
        if self._right is not None:
            self._flush_right()

    def _cancel_right(self) -> None:
        if self._timer:
            self._kill_timer(self._timer)
            self._timer = 0
        self._right = None
        self._right_up = False

    def _flush_right(self, then: list[win32.INPUT] | None = None) -> None:
        right, was_up = self._right, self._right_up
        self._cancel_right()
        if right is None:
            return
        events = [_replay(WM_RBUTTONDOWN, right.x, right.y)]
        if was_up:
            events.append(_replay(WM_RBUTTONUP, right.x, right.y))
        if then:
            events += then
        else:
            # клик был в исходной точке — возвращаем курсор туда, где он сейчас
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            events.append(_replay(WM_MOUSEMOVE, pt.x, pt.y))
        self._send(events)

    # Точки подмены для тестов.
    def _send(self, events: list[win32.INPUT]) -> None:
        win32.send_inputs(events)

    def _set_timer(self, ms: int) -> int:
        return user32.SetTimer(None, 0, ms, None)

    def _kill_timer(self, timer: int) -> None:
        user32.KillTimer(None, timer)
