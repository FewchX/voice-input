"""Обрезка длинной диктовки в «острове»: раньше на длинном тексте уходила в бесконечный цикл и вешала окно."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # окна не создаются, считается только раскладка

from PySide6.QtWidgets import QApplication  # noqa: E402

LONG = (
    "Сегодня проверяем длинную диктовку: текст идёт и идёт, а в окне видны только последние строки. "
    "Раньше на таком тексте обрезка уходила в бесконечный цикл, и окно переставало отвечать. "
    "Поэтому здесь много фраз разной длины, с запятыми, кавычками «ёлочками» и сокращениями, т. е. всем, "
    "что встречается в обычной речи. Ещё немного слов, чтобы переносы строк приходились на разные места, "
    "и в каждой новой длине текста граница последних строк оказывалась чуть в другом месте. "
) * 6


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def test_wrap_tail_terminates_on_every_length(qt):
    from voice_input import ui

    f = ui.font(11.5)
    width = ui.Island.WIDTH - 2 * ui.Island.PAD
    worst = 0.0
    for cut in range(1, len(LONG), 5):
        text = LONG[:cut]
        t0 = time.perf_counter()
        layout, _ = ui.wrap_tail(text, f, width, ui.Island.MAX_LINES)
        worst = max(worst, time.perf_counter() - t0)
        shown = layout.text()
        assert layout.lineCount() <= ui.Island.MAX_LINES
        assert shown.rstrip().endswith(text.rstrip()[-15:].lstrip())
    assert worst < 0.2
