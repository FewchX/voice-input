"""Озвучка Claude Code: короткая фраза голосом Piper, когда Claude закончил или ждёт ответа.

Хуки в ~/.claude/settings.json вызывают `python -m voice_input.claude_hook stop|notify|ask` и передают
JSON в stdin. Хук только записывает задание и запускает фоновый процесс, чтобы не задерживать Claude.
Фон пересказывает ответ (Haiku через `claude -p`), ждёт своей очереди и закрытия микрофона голосового
ввода (чтобы голос не попал в диктовку) и проговаривает фразу. Если открыть микрофон во время речи,
она обрывается.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import win32
from .config import APP_DIR, Config

log = logging.getLogger("voice_input.claude_hook")

PIPER_REPO = "rhasspy/piper-voices"
# Проверенные версии репозиториев с голосами; для остальных берётся последняя.
PIPER_REVISIONS = {
    PIPER_REPO: "c10ece1aade47bb51c153c893d14e5bf8e5b7117",
    "rraaww/ru_piper": "ba4a8c784ff536f71e4dd48bd6815092fb02cd65",  # голоса сообщества, среди них terra
}
QUEUE_MUTEX = "Local\\VoiceInputClaudeSpeak"  # фразы из нескольких сессий говорятся по очереди
CHILD_ENV = "VOICE_INPUT_SPEAK_CHILD"  # стоит у `claude -p` для пересказа — его собственные хуки молчат
JOBS_DIR = APP_DIR / "speak"
MAX_AGE_S = 180  # фраза, которая ждала дольше (диктовка, очередь), уже неактуальна
SUMMARY_TIMEOUT_S = 45
PAUSE_S = 0.35  # пауза между частями фразы

PROMPT = """Преврати сообщение ИИ-ассистента программисту в короткое голосовое уведомление.
{what}
Правила:
- одна фраза по-русски, от 3 до 12 слов;
- если ассистент закончил работу, начни с «Готово:» и скажи главное: что сделано или каков результат;
- если ассистент задаёт пользователю вопрос или ждёт его решения, начни с «Вопрос:» и сформулируй сам вопрос;
- без markdown, кавычек, эмодзи, ссылок, путей к файлам и кода;
- только кириллица, ни одной латинской буквы: английские слова и названия пиши русскими буквами, как они произносятся (README — ридми, git — гит, Claude — Клод, push — пуш, terra — терра);
- ответь только этой фразой.

Сообщение:
{text}"""

TOOL_WORDS = {
    "Bash": "запуск команды",
    "PowerShell": "запуск команды",
    "Edit": "правку файла",
    "MultiEdit": "правку файла",
    "Write": "запись файла",
    "NotebookEdit": "правку блокнота",
    "WebFetch": "открытие страницы",
    "WebSearch": "поиск в интернете",
}


# --------------------------------------------------------------------------- хук (быстро, в процессе Claude)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get(CHILD_ENV):
        return
    if argv[:1] == ["--worker"]:
        _worker(Path(argv[1]))
        return
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        job = make_job(argv[0] if argv else "stop", data)
        if job is None:
            return
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = JOBS_DIR / f"{time.time_ns()}.json"
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        _spawn_worker(path)
    except Exception:
        _setup_log(Config.read())
        log.exception("хук озвучки упал")


def make_job(event: str, data: dict) -> dict | None:
    """Что сказать: {"kind": "done" | "question" | "say", ...}; None — промолчать."""
    job: dict = {"created": time.time()}
    if event == "stop":
        job.update(kind="done", text=data.get("last_assistant_message") or "", notes=turn_notes(data.get("transcript_path")))
    elif event == "ask":
        questions = (data.get("tool_input") or {}).get("questions") or [{}]
        job.update(kind="question", text=questions[0].get("question") or "", notes="")
    elif event == "notify":
        phrase = notification_phrase(data.get("notification_type"), data.get("message") or "")
        if phrase is None:
            return None
        job.update(kind="say", text=phrase)
    else:
        return None
    return job


def notification_phrase(kind: str | None, message: str) -> str | None:
    """Запрос разрешения или ответа. Остальные уведомления (в том числе «жду ввода» через минуту
    после конца хода) пропускаем: конец хода уже озвучил хук Stop."""
    if kind == "permission_prompt" or (not kind and "permission" in message.lower()):
        m = re.search(r"permission to use (\S+)", message)
        tool = m.group(1) if m else ""
        what = TOOL_WORDS.get(tool) or ("действие в подключённом сервисе" if tool.startswith("mcp__") else "")
        return f"Нужно разрешение на {what}." if what else "Нужно разрешение."
    if kind in ("elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"):
        return "Нужен твой ответ."
    return None


def turn_notes(transcript_path: str | None) -> str:
    """Итог хода, который приложение Claude показывает под ответом (вызов wrap_up: recap и ask).
    Формат файла переписки не документирован — при любой неожиданности просто ничего не берём."""
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 2_000_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()[1:]
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            content = (entry.get("message") or {}).get("content")
            if entry.get("type") == "user" and not entry.get("isMeta"):
                blocks = content if isinstance(content, list) else []
                if not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                    return ""  # дошли до сообщения пользователя: в этом ходе итога не было
            if entry.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and str(b.get("name", "")).endswith("wrap_up"):
                        inp = b.get("input") or {}
                        parts = [inp.get("recap") or ""]
                        if inp.get("ask"):
                            parts.append(f"Вопрос пользователю: {inp['ask']}")
                        return "\n".join(p for p in parts if p)
    except Exception:
        pass
    return ""


def _spawn_worker(job: Path) -> None:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    args = [str(pythonw if pythonw.exists() else exe), "-m", "voice_input.claude_hook", "--worker", str(job)]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    io = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    try:
        # Отрываемся от Claude совсем: если он держит хуки в job-объекте, фон не убьют вместе с хуком.
        subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **io)
    except OSError:
        subprocess.Popen(args, creationflags=flags, **io)


# --------------------------------------------------------------------------- фон


def _setup_log(cfg: Config) -> None:
    from .logfile import ExpiringFileHandler

    APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = ExpiringFileHandler(APP_DIR / "claude_speak.log", max(1, cfg.log_minutes) * 60)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _worker(job_path: Path) -> None:
    # pythonw.exe запускает без консоли: stdout/stderr = None ломают прогресс-бар скачивания голоса.
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    cfg = Config.read()
    _setup_log(cfg)
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    finally:
        job_path.unlink(missing_ok=True)
    if not cfg.claude_voice:
        return
    try:
        _speak_job(job, cfg)
    except Exception:
        log.exception("озвучка не удалась")


def _speak_job(job: dict, cfg: Config) -> None:
    # Голос грузится, пока Haiku пересказывает.
    voice: dict = {}
    loader = threading.Thread(target=lambda: voice.update(v=load_voice(cfg.claude_voice)), daemon=True)
    loader.start()
    phrase = phrase_for(job, cfg.claude_voice_summary)
    loader.join()
    if not phrase or "v" not in voice:
        return
    audio, sr = synthesize(voice["v"], phrase, cfg.claude_voice_speed)

    queue = win32.create_mutex(QUEUE_MUTEX)
    try:
        left_ms = int(max(0.0, MAX_AGE_S - (time.time() - job["created"])) * 1000)
        if not win32.acquire_mutex(queue, left_ms):
            log.info("не дождался очереди — пропускаю: %s", phrase)
            return
        try:
            if not wait_mic_closed(job["created"]):
                log.info("микрофон занят слишком долго — пропускаю: %s", phrase)
                return
            log.info("говорю: %s", phrase)
            if not play(audio, sr):
                log.info("открылся микрофон — замолчал")
        finally:
            win32.release_mutex(queue)
    finally:
        win32.close_handle(queue)


def phrase_for(job: dict, use_summary: bool) -> str:
    if job["kind"] == "say":
        return job["text"]
    fallback = "Готово." if job["kind"] == "done" else "У меня вопрос."
    text = job.get("text") or ""
    if job.get("notes"):
        text = f"{text}\n\nИтог от ассистента:\n{job['notes']}".strip()
    if job["kind"] == "question" and not use_summary:
        return f"Вопрос: {clean_for_speech(text)}" if text else fallback
    if not use_summary or not text.strip():
        return fallback
    what = "Ассистент задал пользователю вопрос с вариантами ответа." if job["kind"] == "question" else "Ассистент закончил свой ход."
    return summarize(PROMPT.format(what=what, text=_trim(text))) or fallback


def _trim(text: str, head: int = 3500, tail: int = 1500) -> str:
    """Главное обычно в начале (итог) и в конце (вопрос) — середину длинного ответа выкидываем."""
    return text if len(text) <= head + tail else f"{text[:head]}\n…\n{text[-tail:]}"


def _claude_exe() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "claude.exe"
    return str(winget) if winget.exists() else None


def summarize(prompt: str) -> str | None:
    exe = _claude_exe()
    if exe is None:
        log.warning("не нашёл claude — говорю без пересказа")
        return None
    cmd = [exe, "-p", "--model", "haiku", "--no-session-persistence", "--tools", "",
           "--settings", '{"disableAllHooks": true}']  # fmt: skip
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    t = time.monotonic()
    try:
        r = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=SUMMARY_TIMEOUT_S,
            env={**os.environ, CHILD_ENV: "1"},
            cwd=JOBS_DIR,  # без CLAUDE.md проекта: пересказу он не нужен
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.exception("пересказ не удался")
        return None
    log.info("пересказ за %.1f с (код %d)", time.monotonic() - t, r.returncode)
    lines = r.stdout.decode("utf-8", "replace").strip().splitlines()
    if r.returncode != 0 or not lines:
        return None  # иначе вслух прочитали бы текст ошибки
    return clean_for_speech(lines[0])[:220] or None


# Русский голос латиницу читает невнятно («README» звучит как «ведьмо»), поэтому перед синтезом
# английские слова переписываются кириллицей: частые — по словарю, остальные — по простым правилам.
SPOKEN = {
    "readme": "ридми", "git": "гит", "github": "гитхаб", "claude": "клод", "code": "код", "python": "пайтон",
    "push": "пуш", "commit": "коммит", "pull": "пулл", "request": "реквест", "json": "джейсон", "api": "апи",
    "ok": "окей", "hook": "хук", "hooks": "хуки", "piper": "пайпер", "haiku": "хайку", "windows": "виндоус",
}  # fmt: skip
DIGRAPHS = [("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("ck", "к"), ("ee", "и"), ("oo", "у"),
            ("qu", "кв"), ("kh", "х"), ("zh", "ж"), ("ts", "ц"), ("ya", "я"), ("yu", "ю")]  # fmt: skip
LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz", "а б к д е ф г х и дж к л м н о п к р с т у в в кс й з".split()))


def to_cyrillic(text: str) -> str:
    def word(m: re.Match) -> str:
        low = m.group(0).lower()
        if low in SPOKEN:
            return SPOKEN[low]
        out, i = "", 0
        while i < len(low):
            pair = next(((a, b) for a, b in DIGRAPHS if low.startswith(a, i)), None)
            out, i = (out + pair[1], i + len(pair[0])) if pair else (out + LETTERS[low[i]], i + 1)
        return out

    return re.sub(r"[A-Za-z]+", word, text)


def clean_for_speech(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`*_#>|\[\]«»\"]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def voice_files(name: str) -> tuple[str, str, list[str]]:
    """Имя голоса → (репозиторий, ревизия, файлы).

    «ru_RU-dmitri-medium» — официальный голос Piper; «владелец/репозиторий/имя» — файл «имя.onnx»
    в корне чужого репозитория на Hugging Face (например, «rraaww/ru_piper/ru_RU-terra5871-medium»).
    """
    if name.count("/") == 2:
        owner, repo, stem = name.split("/")
        repo = f"{owner}/{repo}"
        return repo, PIPER_REVISIONS.get(repo, "main"), [stem + ".onnx", stem + ".onnx.json"]
    m = re.fullmatch(r"(([a-z]+)_[A-Z]+)-([^-]+)-([a-z_]+)", name)
    if not m:
        raise ValueError(f"не похоже на имя голоса Piper: {name!r}")
    lang, family, speaker, quality = m.groups()
    base = f"{family}/{lang}/{speaker}/{quality}/{name}"
    return PIPER_REPO, PIPER_REVISIONS[PIPER_REPO], [base + ".onnx", base + ".onnx.json"]


class PiperVoice:
    """Голос Piper по имени (см. voice_files)."""

    def __init__(self, name: str):
        from piper import PiperVoice as Piper

        from .hub import pinned_snapshot

        repo, revision, files = voice_files(name)
        self.voice = Piper.load(Path(pinned_snapshot(repo, revision, files)) / files[0])
        self.sample_rate = self.voice.config.sample_rate

    def say(self, text: str, speed: float):
        import numpy as np
        from piper import SynthesisConfig

        config = SynthesisConfig(length_scale=self.voice.config.length_scale / speed)  # у голоса бывает свой темп
        return np.concatenate([c.audio_float_array for c in self.voice.synthesize(text, config)])


def load_voice(name: str):
    return PiperVoice(name)


def synthesize(voice, phrase: str, speed: float):
    import numpy as np

    speed = max(0.5, min(2.0, speed))
    sr = voice.sample_rate
    # Тишина в начале: Bluetooth-наушники «просыпаются» и съедают первые доли секунды.
    parts = [np.zeros(int(0.3 * sr), np.float32)]
    # Каждую часть («Готово:», предложения) синтезируем отдельно и разделяем паузой — без неё голос тараторит.
    for piece in re.split(r"(?<=[:.!?…;])\s+", to_cyrillic(phrase).strip()):
        if parts[1:]:
            parts.append(np.zeros(int(PAUSE_S * sr), np.float32))
        parts.append(voice.say(piece, speed).astype(np.float32))
    return np.concatenate(parts), sr


def mic_open() -> bool:
    return win32.mutex_exists(win32.MIC_OPEN_MUTEX)


def wait_mic_closed(created: float) -> bool:
    """Ждём, пока закроется микрофон голосового ввода (диктовка или редактор), но не дольше MAX_AGE_S."""
    waited = False
    while mic_open():
        waited = True
        if time.time() - created > MAX_AGE_S:
            return False
        time.sleep(0.2)
    if waited:
        time.sleep(0.8)  # не сразу после вставки текста
        return not mic_open()
    return True


def play(audio, sr: int) -> bool:
    """Проигрывает блоками по 50 мс; False — оборвали, потому что открылся микрофон."""
    import numpy as np
    import sounddevice as sd

    try:
        stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
    except Exception:
        rate = int(sd.query_devices(kind="output")["default_samplerate"])
        audio = np.interp(np.linspace(0, len(audio) - 1, int(len(audio) * rate / sr)), np.arange(len(audio)), audio)
        audio, sr = audio.astype(np.float32), rate
        stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
    block = sr // 20
    with stream:
        for i in range(0, len(audio), block):
            if mic_open():
                stream.abort()
                return False
            stream.write(audio[i : i + block])
    return True


if __name__ == "__main__":
    main()
