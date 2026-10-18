"""Голосовой ввод на русском: двойное «\\» → диктовка → «стоп» → правка → «ок» → вставка."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _setup_logging() -> None:
    from .config import APP_DIR

    APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(APP_DIR / "voice.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    for noisy in ("httpx", "httpcore", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    # pythonw.exe запускает без консоли: stdout/stderr = None ломают прогресс-бары загрузки.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    _setup_logging()

    from . import win32

    if not win32.single_instance("Local\\VoiceInput.SingleInstance"):
        logging.getLogger(__name__).info("уже запущен — выхожу")
        return

    from PySide6.QtWidgets import QApplication

    from .app import Controller
    from .config import Config

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("VoiceInput")
    controller = Controller(app, Config.load())  # noqa: F841 — держим ссылку до выхода
    sys.exit(app.exec())
