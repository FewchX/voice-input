"""Повторный запуск будит работающий экземпляр событием Windows — без окон и без фокуса."""

from voice_input import win32


def test_relaunch_event():
    name = "Local\\VoiceInputTestShow"
    assert not win32.signal_event(name)  # никто не ждёт — уведомлять некого
    handle = win32.create_event(name)
    try:
        assert not win32.wait_event(handle, 0)
        assert win32.signal_event(name)
        assert win32.wait_event(handle, 0)
        assert not win32.wait_event(handle, 0)  # сбрасывается само: одно уведомление на запуск
    finally:
        win32.close_handle(handle)
