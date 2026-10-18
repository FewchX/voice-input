"""Лог, который помнит только последние минуты: в нём текст диктовок и названия окон."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

PRUNE_EVERY_S = 30
_TS_FORMAT = "%Y-%m-%d %H:%M:%S,%f"  # как %(asctime)s


def _timestamp(line: str) -> float | None:
    try:
        return datetime.strptime(line[:23], _TS_FORMAT).timestamp()
    except ValueError:
        return None  # продолжение записи (например, строки трейсбэка)


class ExpiringFileHandler(logging.Handler):
    """Пишет в файл, а записи старше keep_s раз в полминуты из него вычищает."""

    def __init__(self, path: Path, keep_s: float):
        super().__init__()
        self.path = Path(path)
        self.keep_s = keep_s
        self._records: deque[tuple[float, str]] = deque()
        self._load()
        # Старые копии от прежнего RotatingFileHandler — там диктовки за всё время.
        for old in (self.path.with_name(self.path.name + ".1"), self.path.with_name(self.path.name + ".2")):
            old.unlink(missing_ok=True)
        threading.Thread(target=self._prune_loop, name="log-prune", daemon=True).start()

    def _load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines:
            ts = _timestamp(line)
            if ts is not None:
                self._records.append((ts, line))
            elif self._records:
                prev_ts, prev = self._records.pop()
                self._records.append((prev_ts, prev + "\n" + line))
        self._prune()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._records.append((record.created, msg))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            self.handleError(record)

    def _prune(self) -> None:
        cutoff = time.time() - self.keep_s
        if not self._records or self._records[0][0] >= cutoff:
            return
        while self._records and self._records[0][0] < cutoff:
            self._records.popleft()
        text = "".join(msg + "\n" for _, msg in self._records)
        self.path.write_text(text, encoding="utf-8")

    def _prune_loop(self) -> None:
        while True:
            time.sleep(PRUNE_EVERY_S)
            self.acquire()
            try:
                self._prune()
            except Exception:
                pass  # файл открыт в другой программе — почистим в следующий раз
            finally:
                self.release()
