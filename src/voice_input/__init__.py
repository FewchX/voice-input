"""Голосовой ввод на русском: двойное «\\» → диктовка → «стоп» → правка → «ок» → вставка."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _setup_logging(keep_minutes: int) -> None:
    from .config import APP_DIR
    from .logfile import ExpiringFileHandler

    APP_DIR.mkdir(parents=True, exist_ok=True)
    path = APP_DIR / "voice.log"
    if keep_minutes > 0:
        file_handler: logging.Handler = ExpiringFileHandler(path, keep_minutes * 60)
    else:
        file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handlers: list[logging.Handler] = [file_handler]
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

    from .config import Config

    cfg = Config.load()
    _setup_logging(cfg.log_minutes)

    from . import win32

    log = logging.getLogger(__name__)
    log.info("запуск (pid %d)", os.getpid())
    if not win32.single_instance("Local\\VoiceInput.SingleInstance"):
        shown = win32.signal_event(win32.SHOW_EVENT)
        log.info("уже запущен — %s и выхожу", "показал уведомление" if shown else "уведомить не удалось")
        return

    from PySide6.QtWidgets import QApplication

    from .app import Controller

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("VoiceInput")
    controller = Controller(app, cfg)  # noqa: F841 — держим ссылку до выхода
    sys.exit(app.exec())
