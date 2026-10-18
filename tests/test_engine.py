"""Движок на настоящей модели: звук из tests/audio кладётся прямо в очередь, микрофон не открывается."""

import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from conftest import sil, wav
from voice_input import textutils
from voice_input.engine import CHUNK

pytestmark = pytest.mark.model
D = Qt.ConnectionType.DirectConnection


class Recorder:
    def __init__(self, e):
        self.e = e
        self.events: list[tuple] = []
        e.dictation_done.connect(lambda t, s: self.events.append(("done", t, s)), D)
        e.dictation_cancelled.connect(lambda: self.events.append(("cancelled",)), D)
        e.dictation_idle.connect(lambda t: self.events.append(("idle", t)), D)
        e.command_armed.connect(lambda c: self.events.append(("armed", c)), D)
        e.command.connect(lambda c: self.events.append(("command", c)), D)
        e.text_changed.connect(lambda t: self.events.append(("text", t)), D)

    def run(self, mode: str, *parts) -> list[tuple]:
        """parts — имена файлов из tests/audio или секунды тишины."""
        self.events.clear()
        self.e._switch(mode)
        audio = np.concatenate([sil(0.4)] + [sil(p) if isinstance(p, (int, float)) else wav(p) for p in parts])
        for i in range(0, len(audio), CHUNK):
            self.e._q.put(("audio", self.e._sid, audio[i : i + CHUNK]))
        while not self.e._q.empty():
            time.sleep(0.05)
        # Ждём, пока движок допишет последнее распознавание: события перестали появляться.
        seen, quiet_since, deadline = -1, time.monotonic(), time.monotonic() + 30
        while time.monotonic() < deadline:
            if len(self.events) != seen:
                seen, quiet_since = len(self.events), time.monotonic()
            elif time.monotonic() - quiet_since > 1.5:
                break
            time.sleep(0.05)
        return [ev for ev in self.events if ev[0] != "text"]

    def last_text(self) -> str:
        texts = [ev[1] for ev in self.events if ev[0] == "text"]
        return texts[-1] if texts else ""


@pytest.fixture(scope="module")
def rec(engine):
    return Recorder(engine)


# --------------------------------------------------------------------------- запись


def test_enter_at_end_sends_without_editor(rec):
    ev = rec.run("dictation", "long_enter", 2.5)
    assert ev[0] == ("armed", "send")
    _, text, send = ev[-1]
    assert send and text.endswith("спокойно.") and "энтер" not in text.lower()


def test_send_word_at_end(rec):
    _, text, send = rec.run("dictation", "long_send2", 2.5)[-1]
    assert send and not text.lower().endswith("отправить.")


def test_enter_after_pause_as_separate_phrase(rec):
    """Короткое «Энтер.» после паузы модель в тексте абзаца теряет — ловим по концу фразы отдельно."""
    assert rec.run("dictation", "long_chat", 1.2, "enter_ru", 2.5)[-1][2] is True


def test_send_word_mid_sentence_is_text(rec):
    assert rec.run("dictation", "chat_send_mid", 2.5) == []


def test_stop_opens_editor(rec):
    ev = rec.run("dictation", "dict1", 1.2, "stop", 2.5)
    assert ev[-1][0] == "done" and ev[-1][2] is False and not ev[-1][1].lower().endswith("стоп.")


def test_speaking_after_stop_cancels_it(rec):
    """«…стоп» и через полсекунды речь дальше — «стоп» был частью фразы.

    Между словами ~1,2 с тишины (0,95 с в конце stop.wav + 0,26 с в начале long_chat.wav): фраза уже
    закончилась (pause_ms = 700), а секунда ожидания команды — ещё нет.
    """
    ev = rec.run("dictation", "dict1", 1.2, "stop", "long_chat", 2.5)
    assert ("armed", "stop") in ev and ("armed", "") in ev
    assert not [e for e in ev if e[0] == "done"]
    assert "стоп" in rec.last_text().lower()


def test_auto_stop_after_silence(rec):
    rec.e.cfg.auto_stop_s = 3
    try:
        ev = rec.run("dictation", "dict1", 4.0)
    finally:
        rec.e.cfg.auto_stop_s = 20
    assert ev[-1][0] == "idle" and ev[-1][1].startswith("Привет")


def test_cancel_phrase(rec):
    assert rec.run("dictation", "cancel", 1.5) == [("cancelled",)]


def test_replacements_applied(rec):
    _, text, _ = rec.run("dictation", "dev_0", 1.2, "stop", 2.5)[-1]
    assert "git" in text and "гид" not in text
    _, text, _ = rec.run("dictation", "dev_2", 1.2, "stop", 2.5)[-1]
    assert "Claude Code" in text


# --------------------------------------------------------------------------- редактор


@pytest.mark.parametrize(
    "parts, expected",
    [
        (("long_enter",), ["send"]),
        (("long_send",), ["send"]),
        (("long_ok",), ["ok"]),
        (("long_chat",), []),
        (("long_chat", 1.2, "send"), ["send"]),
        (("ok",), ["ok"]),
        (("cancel",), ["cancel"]),
    ],
)
def test_editor_commands(rec, parts, expected):
    ev = rec.run("commands", *parts, 1.5)
    assert [e[1] for e in ev if e[0] == "command"] == expected


@pytest.mark.parametrize("name", ["enter_ru", "enter_e", "enter_i", "enter_tr", "enter_en"])
def test_model_hears_enter_variants(engine, name):
    text = engine._recognize(np.concatenate([sil(0.3), wav(name), sil(0.3)]))
    assert textutils.ends_with(text, engine.cfg.send_words), text
