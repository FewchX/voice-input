"""Лог помнит только последние минуты."""

import logging
import time
from datetime import datetime

from voice_input import logfile


def line(ts: float, text: str) -> str:
    stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S,%f")[:23]
    return f"{stamp} INFO voice_input.engine: {text}"


def test_old_lines_dropped_on_start(tmp_path):
    path = tmp_path / "voice.log"
    now = time.time()
    path.write_text(
        "\n".join([line(now - 600, "старая диктовка"), "Traceback продолжение старой", line(now - 10, "свежая")]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "voice.log.1").write_text("архив", encoding="utf-8")
    logfile.ExpiringFileHandler(path, keep_s=300)
    text = path.read_text(encoding="utf-8")
    assert "старая" not in text and "свежая" in text
    assert not (tmp_path / "voice.log.1").exists()


def test_records_expire(tmp_path):
    path = tmp_path / "voice.log"
    h = logfile.ExpiringFileHandler(path, keep_s=300)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log = logging.getLogger("test-expire")
    log.propagate = False
    log.addHandler(h)
    log.warning("первая")
    log.warning("вторая")
    assert "первая" in path.read_text(encoding="utf-8")
    h._records[0] = (time.time() - 1000, h._records[0][1])  # первая запись «состарилась»
    h._prune()
    text = path.read_text(encoding="utf-8")
    assert "первая" not in text and "вторая" in text
    log.removeHandler(h)
