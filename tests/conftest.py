"""Общее для тестов. Ни один тест не трогает рабочий стол: ни клавиш, ни мыши, ни фокуса, ни буфера обмена.

Звук в tests/audio синтезирован голосом Windows «Microsoft Irina Desktop» (16 кГц, моно);
«enter_en» и «en_*» — английским «Microsoft Zira Desktop».
Тесты с моделью помечены `model` (~1 мин); быстрые: `pytest -m "not model"`.
"""

from __future__ import annotations

import ctypes
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
AUDIO = Path(__file__).resolve().parent / "audio"
sys.path.insert(0, str(ROOT / "src"))


def pytest_configure(config):
    config.addinivalue_line("markers", "model: тесты с моделью распознавания (медленные)")


def wav(name: str) -> np.ndarray:
    with wave.open(str(AUDIO / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def sil(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(seconds * 16_000)) * 0.002).astype(np.float32)


def _engine(language: str):
    """Движок с загруженной моделью; звук подаётся в очередь напрямую, микрофон не открывается."""
    # За компьютером в это время работают — модель не должна отнимать у человека процессор.
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    from voice_input.config import Config
    from voice_input.engine import Engine

    e = Engine(Config(language=language))
    deadline = time.monotonic() + 300  # первый раз модель ещё и скачивается
    while not e.ready:
        assert time.monotonic() < deadline, "модель не загрузилась"
        time.sleep(0.1)
    return e


@pytest.fixture(scope="session")
def engine():
    return _engine("ru")


@pytest.fixture(scope="session")
def engine_en():
    return _engine("en")
