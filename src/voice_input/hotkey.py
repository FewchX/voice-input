"""Глобальный low-level хук клавиатуры: двойное нажатие «\\» и клавиши во время записи.

Первое нажатие «\\» придерживается. Если за `window_ms` пришло второе — это
горячая клавиша, и оба нажатия проглатываются. Если нет (или нажата другая
клавиша) — придержанный «\\» вбрасывается обратно, так что обычный ввод не ломается.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from collections.abc import Callable

from . import win32
from .win32 import kernel32, user32

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN = 0x0100, 0x0101, 0x0104
WM_TIMER, WM_QUIT = 0x0113, 0x0012
LLKHF_EXTENDED = 0x01

TRIGGER_KEYS = {win32.VK_OEM_5, win32.VK_OEM_102}
REINSTALL_EVERY_MS = 30_000


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", win32.ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(win32.LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = win32.LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.SetTimer.argtypes = [wt.HWND, win32.ULONG_PTR, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = win32.ULONG_PTR
user32.KillTimer.argtypes = [wt.HWND, win32.ULONG_PTR]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


class KeyboardHook:
    """Колбэки вызываются из потока хука — внутри них нельзя трогать GUI напрямую."""

    def __init__(
        self,
        on_double_tap: Callable[[], None],
        on_escape: Callable[[], None],
        on_enter: Callable[[], None],
        window_ms: int = 350,
    ):
        self.on_double_tap = on_double_tap
        self.on_escape = on_escape
        self.on_enter = on_enter
        self.window_ms = window_ms
        # Во время записи перехватываем Esc/Enter (окно записи не получает фокус).
        self.capture_keys = False
        # Пока открыт наш редактор, «\\» должен печататься в нём как обычно.
        self.enabled = True

        self._proc = HOOKPROC(self._hook_proc)
        self._hook = None
        self._thread_id = 0
        self._ready = threading.Event()

        self._pending: KBDLLHOOKSTRUCT | None = None
        self._pending_up = False
        self._timer = 0
        self._swallow_up: set[int] = set()

    # -------------------------------------------------------------- жизненный цикл

    def start(self) -> None:
        threading.Thread(target=self._run, name="keyboard-hook", daemon=True).start()
        self._ready.wait(2)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _install(self) -> None:
        """Ставит хук заново: сначала новый, потом снимаем старый — без «дыры» в перехвате.

        Windows молча снимает low-level хук, если тот хоть раз ответил дольше
        LowLevelHooksTimeout (у нас это возможно, пока другой поток держит GIL),
        поэтому переустанавливаем его периодически.
        """
        new = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not new:
            log.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())
            return
        old, self._hook = self._hook, new
        if old:
            user32.UnhookWindowsHookEx(old)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._install()
        self._ready.set()
        watchdog = user32.SetTimer(None, 0, REINSTALL_EVERY_MS, None)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message != WM_TIMER:
                continue
            if msg.wParam == self._timer:
                self._on_timeout()
            elif msg.wParam == watchdog and self._pending is None:
                self._install()
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)

    # -------------------------------------------------------------- логика

    def _hook_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0:
            kb = KBDLLHOOKSTRUCT.from_address(l_param)
            if kb.dwExtraInfo != win32.INJECT_TAG:
                try:
                    if self._handle(kb, w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                        return 1
                except Exception:  # исключение в хуке не должно ломать ввод в системе
                    log.exception("keyboard hook error")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _handle(self, kb: KBDLLHOOKSTRUCT, down: bool) -> bool:
        vk = kb.vkCode
        if not down and vk in self._swallow_up:
            self._swallow_up.discard(vk)
            return True

        if self.capture_keys and down and vk in (win32.VK_ESCAPE, win32.VK_RETURN):
            if not win32.modifiers_down():
                self._swallow_up.add(vk)
                (self.on_escape if vk == win32.VK_ESCAPE else self.on_enter)()
                return True

        if vk in TRIGGER_KEYS and self.enabled:
            return self._handle_trigger(kb, down)

        if self._pending is not None and down:
            # Другая клавиша во время ожидания: сначала отдаём «\\», потом её — порядок сохраняется.
            self._flush_pending()
            win32.send_inputs([self._replay(kb, up=False)])
            return True
        return False

    def _handle_trigger(self, kb: KBDLLHOOKSTRUCT, down: bool) -> bool:
        if self._pending is None:
            if down and not win32.modifiers_down():
                self._pending = KBDLLHOOKSTRUCT(kb.vkCode, kb.scanCode, kb.flags, kb.time, 0)
                self._pending_up = False
                self._timer = user32.SetTimer(None, 0, self.window_ms, None)
                return True
            return False

        if not down:
            if kb.vkCode == self._pending.vkCode and not self._pending_up:
                self._pending_up = True
                return True
            return False

        if not self._pending_up:
            # Клавишу держат — это автоповтор, а не двойное нажатие.
            self._flush_pending()
            return False

        if (kb.time - self._pending.time) & 0xFFFFFFFF <= self.window_ms:
            self._cancel_pending()
            self._swallow_up.add(kb.vkCode)
            self.on_double_tap()
            return True

        self._flush_pending()
        return False

    def _on_timeout(self) -> None:
        if self._pending is not None:
            self._flush_pending()

    def _cancel_pending(self) -> None:
        if self._timer:
            user32.KillTimer(None, self._timer)
            self._timer = 0
        self._pending = None
        self._pending_up = False

    def _flush_pending(self) -> None:
        kb, was_up = self._pending, self._pending_up
        self._cancel_pending()
        if kb is None:
            return
        events = [self._replay(kb, up=False)]
        if was_up:
            events.append(self._replay(kb, up=True))
        win32.send_inputs(events)

    @staticmethod
    def _replay(kb: KBDLLHOOKSTRUCT, up: bool) -> win32.INPUT:
        return win32.key_input(kb.vkCode, kb.scanCode, up=up, extended=bool(kb.flags & LLKHF_EXTENDED))
