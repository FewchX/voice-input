"""Логика жеста ЛКМ → ПКМ → ЛКМ: хук не ставится, события подаются в _handle, SendInput подменён."""

import pytest

from voice_input import mousehook as mh
from voice_input import win32

L, LU, R, RU = mh.WM_LBUTTONDOWN, mh.WM_LBUTTONUP, mh.WM_RBUTTONDOWN, mh.WM_RBUTTONUP
MV, WH = mh.WM_MOUSEMOVE, mh.WM_MOUSEWHEEL
FLAG_NAMES = {0x2: "Ldown", 0x4: "Lup", 0x8: "Rdown", 0x10: "Rup", 0x800: "wheel", 0x20: "Mdown"}


class Probe(mh.MouseGesture):
    def __init__(self):
        self.fired = 0
        super().__init__(self._fire, 400)
        self.sent: list[str] = []
        self.timers: set[int] = set()
        self._next = 100

    def _fire(self):
        self.fired += 1

    def _send(self, events):
        for e in events:
            f = e.mi.dwFlags & 0x1FFE
            self.sent.append(FLAG_NAMES.get(f, "move" if f == 0 else hex(f)))

    def _set_timer(self, ms):
        self._next += 1
        self.timers.add(self._next)
        return self._next

    def _kill_timer(self, t):
        self.timers.discard(t)

    def run(self, events):
        """events: (msg, x, y, t[, data]) или "timeout" → список «проглочено?»."""
        out = []
        for ev in events:
            if ev == "timeout":
                self._on_timeout()
                out.append("T")
                continue
            msg, x, y, t, *rest = ev
            out.append(self._handle(msg, x, y, rest[0] if rest else 0, t))
        return out


@pytest.fixture(autouse=True)
def no_modifiers(monkeypatch):
    monkeypatch.setattr(win32, "modifiers_down", lambda: False)


GESTURE = [(L, 10, 10, 0), (LU, 10, 10, 80), (R, 10, 10, 200), (RU, 10, 10, 250), (L, 10, 10, 320), (LU, 10, 10, 400)]


def test_gesture_fires():
    p = Probe()
    r = p.run(GESTURE[:2] + [(R, 11, 10, 200), (MV, 12, 10, 220), (RU, 12, 10, 250), (L, 12, 10, 320), (LU, 12, 10, 400)])
    assert p.fired == 1 and r == [False, False, True, False, True, True, True] and not p.sent and not p.timers


def test_overlapping_fast_gesture_counts():
    """Левая нажата раньше, чем отпущена правая, — при быстром жесте так бывает."""
    p = Probe()
    r = p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 10, 10, 200), (L, 10, 10, 300), (RU, 10, 10, 330), (LU, 10, 10, 380)])
    assert p.fired == 1 and r == [False, False, True, True, True, True] and not p.sent


def test_plain_right_click_untouched():
    p = Probe()
    assert p.run([(R, 50, 50, 5000), (RU, 50, 50, 5080)]) == [False, False] and not p.sent


def test_slow_right_click_passes():
    p = Probe()
    assert p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 10, 10, 700), (RU, 10, 10, 780)]) == [False] * 4


def test_right_click_elsewhere_passes():
    p = Probe()
    assert p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 200, 10, 200)]) == [False] * 3 and not p.sent


def test_timeout_replays_right_click():
    p = Probe()
    p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 11, 10, 200), (RU, 11, 10, 260), "timeout"])
    assert p.sent == ["Rdown", "Rup", "move"] and p.fired == 0 and not p.timers


def test_right_drag_not_broken():
    p = Probe()
    r = p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 10, 10, 200), (MV, 60, 10, 230), (MV, 80, 10, 240), (RU, 80, 10, 400)])
    assert r == [False, False, True, True, False, False] and p.sent == ["Rdown", "move"]


def test_wheel_after_right_keeps_order():
    p = Probe()
    r = p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 10, 10, 200), (RU, 10, 10, 250), (WH, 10, 10, 300, 0xFF88)])
    assert p.sent == ["Rdown", "Rup", "wheel"] and r[-1] is True


def test_third_click_elsewhere_is_ordinary():
    p = Probe()
    r = p.run([(L, 10, 10, 0), (LU, 10, 10, 80), (R, 10, 10, 200), (RU, 10, 10, 250), (L, 300, 300, 320), (LU, 300, 300, 400)])
    assert p.sent == ["Rdown", "Rup", "Ldown"] and r[-1] is False and p.fired == 0


def test_disabled_in_editor():
    p = Probe()
    p.enabled = False
    assert p.run(GESTURE) == [False] * 6 and p.fired == 0


def test_chord_untouched():
    p = Probe()
    assert p.run([(L, 10, 10, 0), (R, 10, 10, 100), (RU, 10, 10, 150), (LU, 10, 10, 200)]) == [False] * 4


def test_two_gestures_then_plain_click():
    p = Probe()
    p.run(GESTURE)
    p.run([(m, x, y, t + 3000) for m, x, y, t in GESTURE])
    assert p.run([(R, 10, 10, 9000), (RU, 10, 10, 9050)]) == [False, False] and p.fired == 2 and not p.sent


def test_tick_counter_wraparound():
    p = Probe()
    t0 = 0xFFFFFF00
    p.run([(m, x, y, (t0 + t) & 0xFFFFFFFF) for m, x, y, t in GESTURE[:5]])
    assert p.fired == 1


def test_absolute_coordinates_cover_virtual_desktop():
    u = win32.user32
    vx, vy, vw, vh = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    a, b = win32.mouse_input(vx, vy), win32.mouse_input(vx + vw - 1, vy + vh - 1)
    assert (a.mi.dx, a.mi.dy, b.mi.dx, b.mi.dy) == (0, 0, 65535, 65535)
