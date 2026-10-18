"""Вставка и возврат фокуса. Весь win32 подменён: ни одно нажатие и ни один буфер обмена не настоящие."""

import threading
import types

import pytest

from voice_input import inserter, win32
from voice_input.config import Config


class FakeDesktop:
    """Передний план, буфер обмена и нажатия — в памяти."""

    def __init__(self, monkeypatch, focus_ok=True, lose_focus_after=None):
        self.calls: list[tuple] = []
        self.fg = 1
        self.reads = 0
        self.focus_ok = focus_ok
        self.lose_focus_after = lose_focus_after
        stub = dict(
            wait_modifiers_released=lambda: None,
            force_foreground=self.force_foreground,
            wait_foreground=lambda h, timeout=0.8: self.foreground_window() == h,
            foreground_window=self.foreground_window,
            clipboard_snapshot=lambda: self.calls.append(("snapshot",)) or [(13, b"old")],
            clipboard_set_text=lambda text, temporary=True: self.calls.append(("clipboard", text, temporary)) or True,
            clipboard_sequence=lambda: 7,
            clipboard_restore=lambda items: self.calls.append(("restore",)),
            send_ctrl_v=lambda: self.calls.append(("ctrl+v",)),
            send_combo=lambda key: self.calls.append(("key", key)),
            type_unicode=lambda text: self.calls.append(("type", text)),
        )
        for name, fn in stub.items():
            monkeypatch.setattr(win32, name, fn)
        monkeypatch.setattr(inserter, "time", types.SimpleNamespace(sleep=lambda s: None))

    def force_foreground(self, hwnd):
        self.calls.append(("focus", hwnd))
        if self.focus_ok:
            self.fg = hwnd
        return self.focus_ok

    def foreground_window(self):
        self.reads += 1
        if self.lose_focus_after is not None and self.reads > self.lose_focus_after:
            return 999
        return self.fg

    @property
    def kinds(self):
        return [c[0] for c in self.calls]


def test_insert_and_send(monkeypatch):
    d = FakeDesktop(monkeypatch)
    assert inserter.insert_text(42, "привет", "clipboard", "enter")
    assert d.kinds == ["focus", "clipboard", "ctrl+v", "key"]
    assert d.calls[1] == ("clipboard", "привет", False)  # текст остаётся в буфере


def test_focus_not_returned_nothing_typed(monkeypatch):
    d = FakeDesktop(monkeypatch, focus_ok=False)
    assert inserter.insert_text(42, "привет", "clipboard", "enter") is False
    assert not {"ctrl+v", "key", "clipboard"} & set(d.kinds)


def test_restore_clipboard_when_asked(monkeypatch):
    d = FakeDesktop(monkeypatch)
    assert inserter.insert_text(42, "привет", "clipboard", None, keep_in_clipboard=False)
    assert d.kinds == ["focus", "snapshot", "clipboard", "ctrl+v", "restore"]


def test_focus_lost_before_paste(monkeypatch):
    d = FakeDesktop(monkeypatch, lose_focus_after=1)
    assert inserter.insert_text(42, "привет", "clipboard", "enter") is False
    assert "ctrl+v" not in d.kinds


def test_focus_lost_before_enter(monkeypatch):
    d = FakeDesktop(monkeypatch, lose_focus_after=2)
    assert inserter.insert_text(42, "привет", "clipboard", "enter") is True
    assert "ctrl+v" in d.kinds and "key" not in d.kinds


def test_empty_text_sends_nothing(monkeypatch):
    d = FakeDesktop(monkeypatch)
    assert inserter.insert_text(42, "", "clipboard", "enter")
    assert d.kinds == ["focus"]


@pytest.mark.parametrize("send", [False, True])
def test_app_failed_refocus_keeps_text_and_notifies(monkeypatch, send):
    """Вернуться в окно не вышло: ни Ctrl+V, ни Enter, текст — в буфере, уведомление в трее."""
    from voice_input import app

    d = FakeDesktop(monkeypatch, focus_ok=False)
    done = threading.Event()
    notes: list[str] = []
    fake = types.SimpleNamespace(
        state="editing",
        target=42,
        cfg=Config(),
        engine=types.SimpleNamespace(stop=lambda: None),
        _notify=types.SimpleNamespace(emit=notes.append),
        _inserted=types.SimpleNamespace(emit=done.set),
    )
    app.Controller._insert(fake, "текст для чата", send)
    assert done.wait(5)
    assert not {"ctrl+v", "key"} & set(d.kinds)
    assert ("clipboard", "текст для чата", False) in d.calls
    assert notes and "буфер" in notes[0]
