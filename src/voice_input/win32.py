"""Тонкие обёртки над WinAPI: ввод с клавиатуры, фокус окон, буфер обмена."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = wt.WPARAM
LRESULT = wt.LPARAM

# Метка для событий, которые мы сами вбрасываем через SendInput:
# хук пропускает их без обработки.
INJECT_TAG = 0x564F4943  # "VOIC"

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
VK_OEM_5, VK_OEM_102 = 0xDC, 0xE2
VK_V = 0x56


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.OpenClipboard.argtypes = [wt.HWND]
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wt.UINT
user32.GetClipboardSequenceNumber.restype = wt.DWORD
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]


# --------------------------------------------------------------------------- клавиатура


def key_input(vk: int, scan: int = 0, up: bool = False, extended: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=INJECT_TAG)
    return inp


def unicode_input(code_unit: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=flags, time=0, dwExtraInfo=INJECT_TAG)
    return inp


def send_inputs(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def modifiers_down() -> bool:
    return any(is_down(vk) for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def wait_modifiers_released(timeout: float = 1.5) -> None:
    deadline = time.monotonic() + timeout
    while modifiers_down() and time.monotonic() < deadline:
        time.sleep(0.02)


def send_ctrl_v() -> None:
    send_inputs(
        [
            key_input(VK_CONTROL),
            key_input(VK_V),
            key_input(VK_V, up=True),
            key_input(VK_CONTROL, up=True),
        ]
    )


_KEY_NAMES = {
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "shift": VK_SHIFT,
    "alt": VK_MENU,
    "enter": VK_RETURN,
    "return": VK_RETURN,
}


def send_combo(spec: str) -> None:
    """Нажимает сочетание вида "enter" или "ctrl+enter"."""
    keys = [_KEY_NAMES[k.strip().lower()] for k in spec.split("+") if k.strip()]
    down = [key_input(vk) for vk in keys]
    up = [key_input(vk, up=True) for vk in reversed(keys)]
    send_inputs(down + up)


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


def mouse_input(x: int, y: int, flags: int = 0, data: int = 0) -> INPUT:
    """Событие мыши в точке (x, y) экрана: перемещение туда + кнопки/колесо из flags."""
    vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    # Абсолютные координаты SendInput — 0..65535 на весь виртуальный рабочий стол.
    nx = round((x - vx) * 65535 / max(1, vw - 1))
    ny = round((y - vy) * 65535 / max(1, vh - 1))
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(
        dx=nx,
        dy=ny,
        mouseData=data & 0xFFFFFFFF,
        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | flags,
        time=0,
        dwExtraInfo=INJECT_TAG,
    )
    return inp


def type_unicode(text: str) -> None:
    """Печатает текст посимвольно (запасной способ, если вставка из буфера не работает)."""
    data = text.replace("\r\n", "\n").encode("utf-16-le")
    inputs: list[INPUT] = []
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i : i + 2], "little")
        if unit == 0x0A:
            inputs += [key_input(VK_RETURN), key_input(VK_RETURN, up=True)]
        else:
            inputs += [unicode_input(unit), unicode_input(unit, up=True)]
    for i in range(0, len(inputs), 200):
        send_inputs(inputs[i : i + 200])
        time.sleep(0.005)


# --------------------------------------------------------------------------- окна

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SW_RESTORE = 9


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def add_ex_style(hwnd: int, flags: int) -> None:
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | flags)


def force_foreground(hwnd: int) -> bool:
    """Выводит окно на передний план, обходя ограничение foreground lock."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if foreground_window() == hwnd:
        return True
    cur_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    return foreground_window() == hwnd


user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
MONITOR_DEFAULTTONEAREST = 2


def monitor_of(hwnd: int) -> int:
    """HMONITOR монитора, на котором (большей частью) находится окно."""
    if not hwnd:
        return 0
    return int(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) or 0)


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(wt.HWND(hwnd), buf, 128)
    return buf.value


def is_shell_window(hwnd: int) -> bool:
    """Рабочий стол или панель задач — вставлять текст там некуда."""
    return not hwnd or window_class(hwnd) in SHELL_CLASSES


def describe_window(hwnd: int) -> str:
    """«hwnd [класс] заголовок» — для логов."""
    if not hwnd:
        return "0 (нет активного окна)"
    title = ctypes.create_unicode_buffer(128)
    user32.GetWindowTextW(wt.HWND(hwnd), title, 128)
    return f"{hwnd} [{window_class(hwnd)}] {title.value[:60]!r}"


def wait_foreground(hwnd: int, timeout: float = 0.8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if foreground_window() == hwnd:
            return True
        time.sleep(0.02)
    return foreground_window() == hwnd


def single_instance(name: str) -> bool:
    """True, если это первый экземпляр приложения (мьютекс держится до выхода процесса)."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


_mutex = None

# Пока открыт микрофон, существует этот мьютекс: по нему другие программы (например, синтез речи)
# могут молчать, чтобы их звук не попал в диктовку. Если приложение упадёт, Windows уберёт его сама.
MIC_OPEN_MUTEX = "Local\\VoiceInputMicOpen"


def create_mutex(name: str) -> int:
    return kernel32.CreateMutexW(None, False, name) or 0


def close_handle(handle: int) -> None:
    if handle:
        kernel32.CloseHandle(handle)

# --------------------------------------------------------------------------- буфер обмена

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Форматы, данные которых — не HGLOBAL (GDI-объекты и т.п.): их не сохраняем.
_NON_GLOBAL_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}


def _open_clipboard(retries: int = 25) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _hglobal_from_bytes(data: bytes) -> int | None:
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
    if not h:
        return None
    ptr = kernel32.GlobalLock(h)
    if not ptr:
        kernel32.GlobalFree(h)
        return None
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(h)
    return h


def clipboard_snapshot() -> list[tuple[int, bytes]]:
    saved: list[tuple[int, bytes]] = []
    if not _open_clipboard():
        return saved
    try:
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _NON_GLOBAL_FORMATS or 0x300 <= fmt <= 0x3FF:
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            size = kernel32.GlobalSize(h)
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                continue
            try:
                saved.append((fmt, ctypes.string_at(ptr, size)))
            finally:
                kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
    return saved


def _set_formats(items: list[tuple[int, bytes]]) -> None:
    for fmt, data in items:
        h = _hglobal_from_bytes(data)
        if h and not user32.SetClipboardData(fmt, h):
            kernel32.GlobalFree(h)


def clipboard_set_text(text: str, temporary: bool = True) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\x00\x00"
        items = [(CF_UNICODETEXT, data)]
        if temporary:
            # Просим историю буфера (Win+V) и облачный буфер не сохранять наш временный текст.
            no = (0).to_bytes(4, "little")
            items += [
                (user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), b""),
                (user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory"), no),
                (user32.RegisterClipboardFormatW("CanUploadToCloudClipboard"), no),
            ]
        _set_formats(items)
        return True
    finally:
        user32.CloseClipboard()


def clipboard_restore(items: list[tuple[int, bytes]]) -> None:
    if not _open_clipboard():
        return
    try:
        user32.EmptyClipboard()
        _set_formats(items)
    finally:
        user32.CloseClipboard()


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber())
