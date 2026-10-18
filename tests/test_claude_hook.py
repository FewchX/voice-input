"""Озвучка Claude: что хук решает сказать. Без Haiku, без звука и без фоновых процессов."""

import json

import pytest

from voice_input import claude_hook as ch
from voice_input import win32


def test_stop_uses_last_message():
    job = ch.make_job("stop", {"last_assistant_message": "Готово, тесты прошли."})
    assert job["kind"] == "done" and job["text"] == "Готово, тесты прошли." and job["notes"] == ""


def test_ask_takes_first_question():
    data = {"tool_input": {"questions": [{"question": "Какой голос оставить?", "header": "Голос", "options": []}]}}
    assert ch.make_job("ask", data)["text"] == "Какой голос оставить?"


@pytest.mark.parametrize(
    ("kind", "message", "phrase"),
    [
        ("permission_prompt", "Claude needs your permission to use Bash", "Нужно разрешение на запуск команды."),
        ("permission_prompt", "Claude needs your permission to use Edit", "Нужно разрешение на правку файла."),
        ("permission_prompt", "Claude needs your permission to use mcp__x__y", "Нужно разрешение на действие в подключённом сервисе."),
        ("permission_prompt", "Claude needs your permission", "Нужно разрешение."),
        (None, "Claude needs your permission to use Write", "Нужно разрешение на запись файла."),
        ("elicitation_dialog", "", "Нужен твой ответ."),
        ("idle_prompt", "Claude is waiting for your input", None),
        (None, "Claude is waiting for your input", None),
    ],
)
def test_notification_phrase(kind, message, phrase):
    assert ch.notification_phrase(kind, message) == phrase


def test_idle_notification_is_silent():
    assert ch.make_job("notify", {"notification_type": "idle_prompt", "message": "waiting"}) is None


def _transcript(tmp_path, entries):
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
    return str(path)


def _wrap_up(recap, ask=None):
    inp = {"recap": recap, **({"ask": ask} if ask else {})}
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__ccd_turn__wrap_up", "input": inp}]}}


def test_turn_notes_from_wrap_up(tmp_path):
    path = _transcript(
        tmp_path,
        [
            {"type": "user", "message": {"content": "первый вопрос"}},
            _wrap_up("Старый итог."),
            {"type": "user", "message": {"content": "второй вопрос"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            _wrap_up("Сделал.", "Пушить?"),
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            "битая строка",
        ],
    )
    assert ch.turn_notes(path) == "Сделал.\nВопрос пользователю: Пушить?"


def test_turn_notes_ignores_previous_turn(tmp_path):
    path = _transcript(tmp_path, [_wrap_up("Старый итог."), {"type": "user", "message": {"content": "новый вопрос"}}])
    assert ch.turn_notes(path) == ""


def test_turn_notes_missing_file():
    assert ch.turn_notes(r"C:\нет\такого.jsonl") == "" and ch.turn_notes(None) == ""


def test_phrase_without_summary():
    assert ch.phrase_for({"kind": "done", "text": "длинный ответ"}, use_summary=False) == "Готово."
    assert ch.phrase_for({"kind": "question", "text": "**Какой** голос?"}, use_summary=False) == "Вопрос: Какой голос?"
    assert ch.phrase_for({"kind": "say", "text": "Нужно разрешение."}, use_summary=True) == "Нужно разрешение."


def test_summary_failure_falls_back(monkeypatch):
    monkeypatch.setattr(ch, "summarize", lambda prompt: None)
    assert ch.phrase_for({"kind": "done", "text": "что-то сделал"}, use_summary=True) == "Готово."
    assert ch.phrase_for({"kind": "question", "text": "Что дальше?"}, use_summary=True) == "У меня вопрос."


def test_voice_files():
    repo, rev, files = ch.voice_files("ru_RU-dmitri-medium")
    assert repo == "rhasspy/piper-voices" and len(rev) == 40
    assert files == ["ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx", "ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json"]
    with pytest.raises(ValueError):
        ch.voice_files("dmitri")


def test_community_voice_files():
    repo, rev, files = ch.voice_files("rraaww/ru_piper/ru_RU-terra5871-medium")
    assert repo == "rraaww/ru_piper" and len(rev) == 40
    assert files == ["ru_RU-terra5871-medium.onnx", "ru_RU-terra5871-medium.onnx.json"]
    assert ch.voice_files("someone/voices/x")[1] == "main"  # незнакомый репозиторий — последняя версия


def test_default_voice_is_known():
    from voice_input.config import Config

    assert ch.voice_files(Config().claude_voice)[1] != "main"


def test_child_run_is_silent(monkeypatch):
    """`claude -p` для пересказа запускает свои хуки — они не должны порождать новую озвучку."""
    monkeypatch.setenv(ch.CHILD_ENV, "1")
    monkeypatch.setattr(ch, "make_job", lambda *a: pytest.fail("хук не должен ничего делать"))
    ch.main(["stop"])


def test_mic_open_flag():
    name = "Local\\VoiceInputTestFlag"
    assert not win32.mutex_exists(name)
    handle = win32.create_mutex(name)
    try:
        assert win32.mutex_exists(name)
    finally:
        win32.close_handle(handle)
    assert not win32.mutex_exists(name)


def test_latin_spoken_in_cyrillic():
    assert ch.to_cyrillic("Голос terra, README и git push готовы") == "Голос терра, ридми и гит пуш готовы"
    assert ch.to_cyrillic("Shell check") == "шелл чек"
