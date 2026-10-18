"""Настройки: %APPDATA%\\VoiceInput\\config.json (создаётся при первом запуске)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "VoiceInput"
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class Config:
    # Модель распознавания (onnx-asr). e2e-версии GigaAM сами расставляют пунктуацию.
    model: str = "gigaam-v3-e2e-rnnt"
    quantization: str | None = None
    # Микрофон: null — системный по умолчанию, либо номер/часть названия устройства.
    microphone: int | str | None = None

    double_tap_ms: int = 350
    # Жест мышью: быстро ЛКМ → ПКМ → ЛКМ на одном месте (не дольше mouse_gesture_ms между кликами).
    mouse_gesture: bool = True
    mouse_gesture_ms: int = 400
    # Пауза, после которой фраза считается законченной (и проверяется «стоп»).
    pause_ms: int = 700
    vad_threshold: float = 0.5

    # «stop» — модель иногда записывает «стоп» латиницей.
    stop_words: list[str] = field(default_factory=lambda: ["стоп", "stop"])
    ok_words: list[str] = field(default_factory=lambda: ["ок", "окей", "о кей", "ok", "okay", "окэй", "готово"])
    cancel_words: list[str] = field(default_factory=lambda: ["отмена", "отменить"])
    # Вставить и сразу отправить (нажать send_key в исходном окне). Работают и во время записи — без «стоп».
    # «~» перед словом — узнавать его и с одной ошибкой, и латиницей (~энтер: Enter, ентер, интер, энтр…).
    # «Энджер» — так модель слышит английское произношение «enter».
    send_words: list[str] = field(
        default_factory=lambda: ["отправить", "отправь", "отправляй", "отправите", "~энтер", "~энджер"]
    )
    # Чем отправлять: "enter" (мессенджеры, чаты) или "ctrl+enter" (Slack с настройкой, почта и т.п.).
    send_key: str = "enter"

    # Enter в редакторе вставляет текст (Shift+Enter — новая строка, Ctrl+Enter — отправить).
    enter_confirms: bool = True
    # clipboard — через буфер обмена и Ctrl+V; type — посимвольно.
    paste_method: str = "clipboard"
    # Оставлять вставленный текст в буфере обмена (и в истории Win+V) — на случай, если вставка не дошла.
    # false — после вставки вернуть в буфер то, что там было.
    keep_in_clipboard: bool = True
    # Стиль окон: island | siri | dark_glass | glass | light (переключается в меню трея).
    theme: str = "dark_glass"
    # Цвет свечения для стилей с подсветкой: white | orange | blue | purple | pink | green | rainbow
    # или «#RRGGBB»; null — как задумано в стиле.
    glow: str | None = None

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for key, value in data.items():
                    if key in known:
                        setattr(cfg, key, value)
            except Exception:
                log.exception("не удалось прочитать %s — использую настройки по умолчанию", CONFIG_PATH)
        cfg.save()  # дописываем новые ключи, если файл старый
        return cfg

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
