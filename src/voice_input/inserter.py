"""Вставка готового текста в окно, где был курсор до начала диктовки."""

from __future__ import annotations

import logging
import time

from . import win32

log = logging.getLogger(__name__)


def return_focus(hwnd: int) -> bool:
    for _ in range(2):
        if win32.force_foreground(hwnd) and win32.wait_foreground(hwnd):
            return True
        time.sleep(0.1)
    log.warning("не удалось вернуть фокус окну %s", hwnd)
    return False


def _still_there(hwnd: int, what: str) -> bool:
    """Перед каждым нажатием проверяем, что фокус всё ещё в нужном окне — иначе текст уйдёт не туда."""
    if win32.foreground_window() == hwnd:
        return True
    log.warning("фокус ушёл из окна %s — не %s", hwnd, what)
    return False


def insert_text(
    hwnd: int, text: str, method: str = "clipboard", send_key: str | None = None, keep_in_clipboard: bool = True
) -> bool:
    """Вставляет текст; если задан send_key ("enter", "ctrl+enter"), сразу же отправляет.

    False — окно не удалось вернуть на передний план: ничего не вставлено и не отправлено.
    keep_in_clipboard — оставить текст в буфере обмена (на случай, если вставка не дошла).
    """
    win32.wait_modifiers_released()
    if not return_focus(hwnd):
        return False
    if not text:
        return True
    log.info("вставляю %d символов (%s)%s в окно %s", len(text), method, " и отправляю" if send_key else "", hwnd)
    time.sleep(0.08)  # даём приложению восстановить фокус внутри окна

    saved = None if keep_in_clipboard else win32.clipboard_snapshot()
    in_clipboard = win32.clipboard_set_text(text, temporary=not keep_in_clipboard)
    if not _still_there(hwnd, "вставляю"):
        return False

    if method == "type" or not in_clipboard:
        if not in_clipboard:
            log.warning("буфер обмена занят — печатаю посимвольно")
        win32.type_unicode(text)
        if send_key:
            time.sleep(0.1)
            if _still_there(hwnd, "отправляю"):
                win32.send_combo(send_key)
        return True

    seq = win32.clipboard_sequence()
    win32.send_ctrl_v()
    if send_key:
        # Отправляем только после того, как приложение успело вставить текст.
        time.sleep(0.25)
        if _still_there(hwnd, "отправляю"):
            win32.send_combo(send_key)
    if saved is not None:
        # Многие приложения читают буфер асинхронно — не торопимся его восстанавливать.
        time.sleep(0.45)
        if win32.clipboard_sequence() == seq:
            win32.clipboard_restore(saved)
    return True
