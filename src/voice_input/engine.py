"""Микрофон → Silero VAD → GigaAM. Работает в фоновом потоке, наружу — Qt-сигналы.

Режимы:
  dictation — копим фразы, показываем текст вживую; «стоп» в конце фразы завершает диктовку,
              а фраза целиком «отмена» — отменяет;
  commands  — в редакторе ждём команду («ок», «отправить», «отмена») последним словом фразы.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import wave
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Signal

from . import textutils
from .config import Config

log = logging.getLogger(__name__)

SR = 16_000
CHUNK = 512  # 32 мс — родной размер окна Silero VAD для 16 кГц
CHUNK_MS = CHUNK * 1000 // SR

PARTIAL_EVERY_S = 1.0  # не чаще; при долгом распознавании интервал растёт сам
PARAGRAPH_MAX_S = 14.0  # дальше фиксируем абзац: модель держит ~20–30 с за раз
COMMAND_TAIL_S = 5.0  # в редакторе распознаём только конец фразы — там команда
CANCEL_MAX_S = 2.5  # «отмена» во время записи — короткая фраза; длинные не проверяем
GAP = np.zeros(int(0.15 * SR), dtype=np.float32)


# Проверенные версии моделей на Hugging Face. Без ревизии берётся последняя, и обновление у автора
# могло бы незаметно поменять распознавание. Скачиваются при первом запуске в кэш Hugging Face.
PINNED_MODELS = {
    "gigaam-v3-e2e-rnnt": (
        "istupakov/gigaam-v3-onnx",
        "322c3b29492673eb7d0b434bfa9dfb8653e34d02",
        ["config.json", "v3_e2e_rnnt_encoder.onnx", "v3_e2e_rnnt_decoder.onnx", "v3_e2e_rnnt_joint.onnx",
         "v3_e2e_rnnt_vocab.txt"],
    ),
    "silero": ("istupakov/silero-vad-onnx", "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6", ["config.json", "silero_vad.onnx"]),
}


def pinned_model_path(name: str) -> str | None:
    """Папка с закреплённой версией модели (скачивается, если её ещё нет). None — модель не из списка:
    её как обычно найдёт и скачает onnx-asr (последнюю версию)."""
    if name not in PINNED_MODELS:
        return None
    from huggingface_hub import snapshot_download

    repo, revision, files = PINNED_MODELS[name]
    try:
        path = snapshot_download(repo, revision=revision, allow_patterns=files, local_files_only=True)
        if all(os.path.isfile(os.path.join(path, f)) for f in files):
            return path
    except Exception:
        pass  # ещё не скачана
    log.info("скачиваю модель %s (%s, ревизия %s)…", name, repo, revision[:7])
    return snapshot_download(repo, revision=revision, allow_patterns=files)


def _session_options(threads: int):
    """Без «прокрутки» потоков: по умолчанию ONNX Runtime держит их в активном ожидании,
    и короткие частые вызовы (VAD — 31 раз в секунду) съедают четверть процессора впустую."""
    import onnxruntime as rt

    o = rt.SessionOptions()
    o.intra_op_num_threads = threads
    o.inter_op_num_threads = 1
    o.add_session_config_entry("session.intra_op.allow_spinning", "0")
    o.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return o


class SileroStream:
    def __init__(self, session):
        self._session = session
        self._sr = np.array(SR, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(64, dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk])[None, :]
        out, self._state = self._session.run(
            ["output", "stateN"], {"input": x, "state": self._state, "sr": self._sr}
        )
        self._context = chunk[-64:]
        return float(out[0, 0])


class Segmenter:
    """Режет поток на фразы по паузам."""

    def __init__(self, vad: SileroStream, threshold: float, pause_ms: int):
        self.vad = vad
        self.threshold = threshold
        self.neg_threshold = max(threshold - 0.15, 0.05)
        self.pause_chunks = max(pause_ms // CHUNK_MS, 1)
        self.min_chunks = 200 // CHUNK_MS
        self.max_chunks = 25_000 // CHUNK_MS
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self.preroll: deque[np.ndarray] = deque(maxlen=10)  # ~320 мс до начала речи
        self.chunks: list[np.ndarray] = []
        self.in_speech = False
        self.silence = 0
        self.voiced = 0

    def current(self) -> np.ndarray | None:
        return np.concatenate(self.chunks) if self.in_speech and self.chunks else None

    def feed(self, chunk: np.ndarray) -> np.ndarray | None:
        """Возвращает аудио фразы, когда она закончилась."""
        p = self.vad(chunk)
        if not self.in_speech:
            self.preroll.append(chunk)
            if p >= self.threshold:
                self.in_speech = True
                self.chunks = list(self.preroll)
                self.preroll.clear()
                self.silence = 0
                self.voiced = 1
            return None

        self.chunks.append(chunk)
        if p < self.neg_threshold:
            self.silence += 1
        else:
            self.silence = 0
            self.voiced += 1

        if self.silence >= self.pause_chunks or len(self.chunks) >= self.max_chunks:
            return self.cut()
        return None

    def cut(self) -> np.ndarray | None:
        """Принудительно завершает текущую фразу."""
        if not self.in_speech:
            return None
        keep = len(self.chunks) - max(self.silence - 6, 0)  # оставляем ~200 мс тишины в хвосте
        audio = np.concatenate(self.chunks[:keep]) if keep > 0 else None
        voiced = self.voiced
        self.in_speech = False
        self.chunks = []
        self.silence = 0
        self.voiced = 0
        if audio is None or voiced < self.min_chunks:
            return None
        return audio


# Микрофон Bluetooth-наушников работает только через профиль Hands-Free: пока он открыт,
# звук в наушниках становится «телефонным» — моно и глухим.
_BT_MARKERS = ("bthhfenum", "hands-free", "головной телефон")
# «Переназначение звуковых устр.» и «Первичный драйвер» следуют за системным устройством, микшер — не микрофон.
_NOT_A_MIC = ("переназначение", "sound mapper", "первичный драйвер", "primary sound", "стерео микшер", "stereo mix")


def _paren(name: str) -> str:
    """«Микрофон (LE-Bose QC Ultra He» → «LE-Bose QC Ultra He» (MME обрезает имена до 31 символа)."""
    i = name.rfind("(")
    return name[i + 1 :].rstrip(")").strip() if i >= 0 else ""


def pick_non_bluetooth(devices: list[dict], default: int) -> int:
    """Если системный микрофон — Bluetooth-наушники, возвращает другой микрофон того же типа драйвера."""
    # Имена Bluetooth-устройств видны в WDM-KS: «Головной телефон (@...bthhfenum.sys...;(LE-Bose QC Ultra Headphones))».
    bt_names = {_paren(d["name"]) for d in devices if any(m in d["name"].lower() for m in _BT_MARKERS)}
    bt_names.discard("")

    def bluetooth(name: str) -> bool:
        low, part = name.lower(), _paren(name)
        return any(m in low for m in _BT_MARKERS) or (len(part) >= 6 and any(b.startswith(part) for b in bt_names))

    if not bluetooth(devices[default]["name"]):
        return default
    api = devices[default]["hostapi"]
    for i, d in enumerate(devices):
        name = d["name"].lower()
        if (
            d["hostapi"] == api
            and d["max_input_channels"] > 0
            and not bluetooth(d["name"])
            and not any(m in name for m in _NOT_A_MIC)
        ):
            return i
    return default  # другого микрофона нет — пусть будет телефонный режим, чем никакого


class _FakeMic:
    """Для тестов: «микрофон», который в реальном времени читает WAV (16 кГц, моно, 16 бит)."""

    def __init__(self, path: str, callback):
        with wave.open(path) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        self._audio = pcm.astype(np.float32) / 32768
        self._callback = callback
        self._stopped = threading.Event()
        threading.Thread(target=self._run, name="fake-mic", daemon=True).start()

    def _run(self) -> None:
        pos = 0
        while not self._stopped.wait(CHUNK / SR):
            block = self._audio[pos : pos + CHUNK]
            if len(block) < CHUNK:
                block = np.concatenate([block, np.zeros(CHUNK - len(block), dtype=np.float32)])
            pos += CHUNK
            self._callback(block[:, None], CHUNK, None, None)

    def stop(self) -> None:
        self._stopped.set()

    def close(self) -> None:
        self._stopped.set()


class Engine(QObject):
    model_state = Signal(str)  # "loading" | "ready" | "error"
    text_changed = Signal(str)
    dictation_done = Signal(str, bool)  # текст; True — сразу вставить и отправить
    dictation_cancelled = Signal()  # во время записи сказали «отмена»
    dictation_idle = Signal(str)  # auto_stop_s без речи — запись остановлена, вот что успели надиктовать
    command_armed = Signal(str)  # "stop" | "send" — ждём тишины, чтобы выполнить; "" — речь продолжилась
    command = Signal(str)  # "ok" | "send" | "cancel"
    mic_error = Signal(str)
    mic_live = Signal()  # микрофон начал отдавать настоящий звук

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.level = 0.0  # громкость 0..1 для анимации
        self.ready = False
        # Bluetooth-гарнитуре нужно время, чтобы включить микрофон: до этого приходит тишина,
        # и сказанное в первые мгновения теряется. Пока звука нет, окно показывает «Подготовка».
        self.live = False
        self._opened_at = 0.0
        self._first_data_ms: int | None = None

        self._q: queue.Queue = queue.Queue()
        self._sid = 0
        self._stream = None
        self._loaded = threading.Event()
        self._asr = None
        self._vad_session = None

        threading.Thread(target=self._load, name="asr-load", daemon=True).start()
        threading.Thread(target=self._worker, name="asr-worker", daemon=True).start()

    # ------------------------------------------------------------------ API (главный поток)

    def begin_dictation(self) -> bool:
        self._switch("dictation")
        return self._open_mic()

    def begin_commands(self) -> None:
        self._switch("commands")

    def finish_dictation(self) -> None:
        self._q.put(("finish", self._sid))

    def stop(self) -> None:
        self._switch("idle")
        self._close_mic()

    # ------------------------------------------------------------------ микрофон

    def _switch(self, mode: str) -> None:
        sid = self._sid + 1
        self._q.put(("mode", sid, mode))
        self._sid = sid

    def _resolve_device(self):
        import sounddevice as sd

        dev = self.cfg.microphone
        if isinstance(dev, int):
            return dev
        devices = sd.query_devices()
        if dev is not None:
            for i, info in enumerate(devices):
                if info["max_input_channels"] > 0 and str(dev).lower() in info["name"].lower():
                    return i
            log.warning("микрофон %r не найден — беру системный", dev)
        default = sd.default.device[0]
        if not self.cfg.avoid_bluetooth_mic or default is None or default < 0:
            return None
        pick = pick_non_bluetooth(list(devices), default)
        if pick != default:
            log.info(
                "системный микрофон %r — Bluetooth (наушники ушли бы в телефонный режим), беру %r",
                devices[default]["name"],
                devices[pick]["name"],
            )
            return pick
        return None

    def _open_mic(self) -> bool:
        import sounddevice as sd

        if self._stream is not None:
            return True
        self.live = False
        self._first_data_ms = None
        self._opened_at = time.monotonic()
        if fake := os.environ.get("VOICE_INPUT_FAKE_MIC"):
            self._native_sr = SR
            self._stream = _FakeMic(fake, self._on_audio)
            return True
        device = self._resolve_device()
        try:
            stream = sd.InputStream(
                samplerate=SR, channels=1, dtype="float32", blocksize=CHUNK, device=device, callback=self._on_audio
            )
            self._native_sr = SR
        except Exception:
            # Устройство не умеет 16 кГц — пишем в родной частоте и пересэмплируем.
            rate = int(sd.query_devices(device, "input")["default_samplerate"])
            try:
                stream = sd.InputStream(
                    samplerate=rate, channels=1, dtype="float32", device=device, callback=self._on_audio
                )
            except Exception as e:
                log.exception("не удалось открыть микрофон")
                self.mic_error.emit(str(e))
                return False
            self._native_sr = rate
        stream.start()
        self._stream = stream
        name = sd.query_devices(device, "input")["name"]
        log.info("микрофон %r открыт за %d мс (%d Гц)", name, (time.monotonic() - self._opened_at) * 1000, self._native_sr)
        return True

    def _close_mic(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("ошибка при закрытии микрофона")
        self.level = 0.0

    def _on_audio(self, indata, frames, time_info, status) -> None:
        x = indata[:, 0].copy()
        if not self.live:
            ms = int((time.monotonic() - self._opened_at) * 1000)
            if self._first_data_ms is None:
                self._first_data_ms = ms
            if np.max(np.abs(x)) > 1e-5:  # пока гарнитура включается, приходят ровные нули
                self.live = True
                log.info("микрофон: первые данные через %d мс, звук через %d мс", self._first_data_ms, ms)
                self.mic_live.emit()
        if self._native_sr != SR:
            n = int(round(len(x) * SR / self._native_sr))
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        target = min(max((20 * np.log10(rms) + 52) / 36, 0.0), 1.0)
        self.level = target if target > self.level else self.level * 0.7 + target * 0.3
        self._q.put(("audio", self._sid, x))

    # ------------------------------------------------------------------ модель

    def _load(self) -> None:
        self.model_state.emit("loading")
        try:
            import onnx_asr

            t = time.monotonic()
            asr_threads = max(1, min(8, (os.cpu_count() or 2) // 2))
            vad_path = pinned_model_path("silero")
            self._vad_session = onnx_asr.load_vad("silero", vad_path, sess_options=_session_options(1))._model
            asr_path = pinned_model_path(self.cfg.model) if self.cfg.quantization is None else None
            self._asr = onnx_asr.load_model(
                self.cfg.model, asr_path, quantization=self.cfg.quantization, sess_options=_session_options(asr_threads)
            )
            self._asr.recognize(np.zeros(SR, dtype=np.float32), sample_rate=SR)  # прогрев
            log.info("модель %s загружена за %.1f с", self.cfg.model, time.monotonic() - t)
        except Exception:
            log.exception("не удалось загрузить модель")
            self.model_state.emit("error")
            return
        self.ready = True
        self._loaded.set()
        self.model_state.emit("ready")

    def _recognize(self, audio: np.ndarray) -> str:
        try:
            return str(self._asr.recognize(np.ascontiguousarray(audio, dtype=np.float32), sample_rate=SR)).strip()
        except Exception:
            log.exception("ошибка распознавания")
            return ""

    def _clean(self, text: str) -> str:
        return textutils.clean(text, self.cfg.replacements, self.cfg.remove_fillers)

    # ------------------------------------------------------------------ фоновый поток

    def _worker(self) -> None:
        self._loaded.wait()
        seg = Segmenter(SileroStream(self._vad_session), self.cfg.vad_threshold, self.cfg.pause_ms)
        mode, sid = "idle", 0
        buf = np.zeros(0, dtype=np.float32)
        phrases: list[np.ndarray] = []  # фразы текущего абзаца
        committed = ""  # текст зафиксированных абзацев
        pending = ""  # текст текущего абзаца
        next_partial = 0.0

        def paragraph() -> np.ndarray:
            parts: list[np.ndarray] = []
            for a in phrases:
                if parts:
                    parts.append(GAP)
                parts.append(a)
            return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

        armed: str | None = None  # "stop" | "send": команда сказана, ждём тишины, чтобы выполнить
        armed_quiet = 0  # чанков тишины с тех пор
        idle = 0  # чанков без речи подряд — для автоостановки

        def command_words(command: str | None) -> list[str]:
            return self.cfg.send_words if command == "send" else self.cfg.stop_words

        def finish(send: bool = False) -> None:
            nonlocal mode, armed
            words = command_words("send" if send or armed == "send" else "stop")
            text, _ = textutils.strip_trailing_word(textutils.join_text(committed, pending), words)
            mode, armed = "idle", None
            self.dictation_done.emit(self._clean(text), send)

        while True:
            item = self._q.get()
            kind = item[0]

            if kind == "mode":
                _, sid, mode = item
                seg.reset()
                buf = np.zeros(0, dtype=np.float32)
                phrases, committed, pending = [], "", ""
                armed, armed_quiet, idle = None, 0, 0
                continue

            if item[1] != sid:
                continue  # хвост предыдущего сеанса

            if kind == "finish":
                if mode == "dictation":
                    tail = seg.cut()
                    if tail is not None:
                        phrases.append(tail)
                    if phrases:
                        pending = self._recognize(paragraph())
                    finish()
                continue

            if mode == "idle":
                continue

            buf = np.concatenate([buf, item[2]])
            while len(buf) >= CHUNK:
                chunk, buf = buf[:CHUNK], buf[CHUNK:]
                phrase = seg.feed(chunk)

                if mode == "commands":
                    if phrase is not None:
                        # Команда — последнее слово перед паузой, даже если до неё ещё что-то говорили.
                        # Для этого хватает хвоста фразы: так длинная болтовня не тормозит ответ.
                        text = self._recognize(phrase[-int(COMMAND_TAIL_S * SR) :])
                        log.info("команда? %r", text)
                        for command, words in (
                            ("send", self.cfg.send_words),
                            ("ok", self.cfg.ok_words),
                            ("cancel", self.cfg.cancel_words),
                        ):
                            if textutils.ends_with(text, words):
                                self.command.emit(command)
                                break
                    continue

                # mode == "dictation"
                idle = 0 if seg.in_speech else idle + 1
                if self.cfg.auto_stop_s and idle * CHUNK_MS >= self.cfg.auto_stop_s * 1000:
                    text = self._clean(textutils.join_text(committed, pending))
                    log.info("%d с без речи — останавливаю запись (%d символов)", self.cfg.auto_stop_s, len(text))
                    mode, armed = "idle", None
                    self.dictation_idle.emit(text)
                    break

                if armed and phrase is None:
                    if seg.in_speech:
                        # Продолжил говорить — «стоп»/«отправить» было частью речи.
                        log.info("речь продолжилась — «%s» отменён", armed)
                        armed = None
                        self.command_armed.emit("")
                    else:
                        armed_quiet += 1
                        if armed_quiet * CHUNK_MS >= self.cfg.command_wait_ms:
                            log.info("тишина после «%s» — выполняю", armed)
                            finish(send=armed == "send")
                            break
                    continue

                if phrase is not None:
                    phrases.append(phrase)
                    pending = self._recognize(paragraph())
                    # Конец фразы распознаём ещё и отдельно: в тексте абзаца короткое слово после паузы
                    # («…спокойно. Энтер.») модель иногда теряет, а границ фраз в нём не видно.
                    tail = phrase[-int(COMMAND_TAIL_S * SR) :]
                    alone = pending if len(phrases) == 1 and len(tail) == len(phrase) else self._recognize(tail)
                    if len(phrase) <= CANCEL_MAX_S * SR and textutils.is_command(alone, self.cfg.cancel_words):
                        # Фраза целиком «отмена» — отменяем запись.
                        log.info("фраза: %r — отменяю запись", alone)
                        mode = "idle"
                        self.dictation_cancelled.emit()
                        break
                    full = textutils.join_text(committed, pending)
                    db = 20 * np.log10(float(np.sqrt(np.mean(phrase * phrase))) + 1e-9)
                    log.info("фраза (%.1f с, %d дБ): %r", len(phrase) / SR, db, pending)
                    if alone is not pending:
                        log.info("конец фразы отдельно: %r", alone)

                    def said(words: list[str]) -> bool:
                        return textutils.strip_trailing_word(full, words)[1] or textutils.ends_with(alone, words)

                    # «…стоп» — в редактор, «…отправить» / «…энтер» — сразу вставить и отправить.
                    # Выполняем не сразу, а после command_wait_ms тишины: если речь продолжится,
                    # значит, слово было частью фразы.
                    armed = "stop" if said(self.cfg.stop_words) else "send" if said(self.cfg.send_words) else None
                    shown = full
                    if armed:
                        armed_quiet = 0
                        shown = textutils.strip_trailing_word(full, command_words(armed))[0]
                        log.info("в конце «%s» — жду %d мс тишины", armed, self.cfg.command_wait_ms)
                        self.command_armed.emit(armed)
                    self.text_changed.emit(self._clean(shown))
                    if sum(len(p) for p in phrases) >= PARAGRAPH_MAX_S * SR:
                        committed, pending, phrases = full, "", []
                elif seg.in_speech and time.monotonic() >= next_partial:
                    current = seg.current()
                    if current is not None and len(current) >= 0.6 * SR:
                        # Черновик — только по текущей фразе: предыдущие уже распознаны,
                        # а весь абзац целиком перераспознаётся один раз, в конце фразы.
                        t = time.monotonic()
                        partial = self._recognize(current)
                        spent = time.monotonic() - t
                        next_partial = t + max(PARTIAL_EVERY_S, 3 * spent)
                        self.text_changed.emit(self._clean(textutils.join_text(committed, textutils.join_text(pending, partial))))
