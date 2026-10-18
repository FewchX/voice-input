"""Озвучка Claude: что хук решает сказать. Без Haiku, без звука и без фоновых процессов."""

import json
import re
import shutil
import subprocess
from pathlib import Path

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


@pytest.mark.parametrize(
    ("prompt", "mode", "rest"),
    [
        ("Озвучивай.", "all", ""),
        ("озвучивай всё", "all", ""),
        ("Озвучивай вопросы", "questions", ""),
        ("Озвучивай только вопросы.", "questions", ""),
        ("Не озвучивай", "off", ""),
        ("Молчи!", "off", ""),
        ("Озвучивай. Почини тесты в engine.py", "all", "Почини тесты в engine.py"),
        ("Почини тесты, а потом озвучивай", None, "Почини тесты, а потом озвучивай"),
        ("Озвучиваемые фразы слишком длинные", None, "Озвучиваемые фразы слишком длинные"),
        ("Молчит микрофон", None, "Молчит микрофон"),
    ],
)
def test_parse_voice_command(prompt, mode, rest):
    assert ch.parse_voice_command(prompt) == (mode, rest)


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(ch, "APP_DIR", tmp_path)
    spoken = []
    monkeypatch.setattr(ch, "enqueue", lambda job: spoken.append(job and job["text"]))
    return spoken


def test_session_modes(sessions):
    assert ch.session_mode("a", "off") == "off" and ch.session_mode("a", "questions") == "questions"
    ch.set_session_mode("a", "all")
    ch.set_session_mode("b", "questions")
    assert ch.session_mode("a", "off") == "all" and ch.session_mode("b", "off") == "questions"
    assert ch.session_mode(None, "bogus") == "off"


def test_prompt_command_only_is_blocked(sessions, capsys):
    ch.handle_prompt({"session_id": "s1", "prompt": "Озвучивай вопросы."})
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "только вопросы" in out["reason"]
    assert ch.session_mode("s1", "off") == "questions" and sessions == ["Буду озвучивать только вопросы."]


def test_prompt_with_task_goes_through(sessions, capsys):
    ch.handle_prompt({"session_id": "s1", "prompt": "Молчи. И почини тесты"})
    out = capsys.readouterr().out
    assert out.isascii()
    data = json.loads(out)
    assert "decision" not in data and "выключена" in data["hookSpecificOutput"]["additionalContext"]
    assert ch.session_mode("s1", "all") == "off"


def test_ordinary_prompt_untouched(sessions, capsys):
    ch.handle_prompt({"session_id": "s1", "prompt": "Почини тесты"})
    assert capsys.readouterr().out == "" and sessions == [] and ch.session_mode("s1", "x") == "off"


def test_modes_filter_jobs():
    done = {"last_assistant_message": "Всё сделал, тесты прошли."}
    asking = {"last_assistant_message": "Сделал половину. Пушить в main?"}
    ask = {"tool_input": {"questions": [{"question": "Какой голос?"}]}}
    perm = {"notification_type": "permission_prompt", "message": "Claude needs your permission to use Bash"}
    assert all(ch.make_job(e, d, "off") is None for e, d in (("stop", done), ("ask", ask), ("notify", perm)))
    assert ch.make_job("stop", done, "questions") is None
    job = ch.make_job("stop", asking, "questions")
    assert job["kind"] == "question" and job["questions_only"]
    assert ch.make_job("ask", ask, "questions") and ch.make_job("notify", perm, "questions")
    assert ch.make_job("stop", done, "all")["kind"] == "done"


def _git_bash():
    git = shutil.which("git")
    for parent in Path(git).resolve().parents if git else []:
        if (parent / "bin" / "bash.exe").exists():
            return parent / "bin" / "bash.exe"
    return None


@pytest.mark.skipif(_git_bash() is None, reason="нужен Git Bash: так Claude Code запускает хуки на Windows")
@pytest.mark.parametrize("prompt, called", [
    ("Почини тесты", False),
    ("Озвучивай вопросы", True),
    ("и молчи", True),
    (r"Озвучивай", True),  # кириллица, экранированная в JSON
    (r"молчи", True),
])
def test_prompt_prefilter(tmp_path, prompt, called):
    """Шелл-фильтр зовёт Python только для сообщений со словами команд."""
    script = (Path(__file__).parents[1] / "tools" / "claude_speak_prompt.sh").read_text(encoding="utf-8")
    script = re.sub(r"printf .*? prompt ;;", "echo CALLED ;;", script)
    (tmp_path / "f.sh").write_text(script, encoding="utf-8", newline="\n")
    payload = '{"session_id":"s","prompt":"%s"}' % prompt
    r = subprocess.run([str(_git_bash()), "-c", ". ./f.sh"], cwd=tmp_path, input=payload.encode("utf-8"),
                       capture_output=True, timeout=20)
    assert r.returncode == 0 and (b"CALLED" in r.stdout) == called
