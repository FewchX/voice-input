"""Запуск без консоли: двойной клик или `pythonw.exe run.pyw` (так же стартует автозапуск)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

# Двойной клик открывает .pyw системным Python, а зависимости стоят в .venv — перезапускаемся там.
if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve() and VENV_PYTHONW.exists():
    subprocess.Popen([str(VENV_PYTHONW), str(Path(__file__).resolve())], cwd=ROOT)
    sys.exit(0)

sys.path.insert(0, str(ROOT / "src"))

from voice_input import main  # noqa: E402

main()
