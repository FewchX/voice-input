"""Сценарий: «\\\\» → запись → «стоп» → редактор → «ок» → вставка в исходное окно."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import winreg
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import inserter, win32
from .config import CONFIG_PATH, Config
from .engine import Engine
from .hotkey import KeyboardHook
from .mousehook import MouseGesture
from .themes import GLOWS, THEMES, Backdrop, get_theme, glow_key, with_glow
from .ui import Editor, Island, backdrop_area, make_tray_icon

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "VoiceInput"


class Controller(QObject):
    # Мост из потока хука клавиатуры в главный поток Qt.
    _hotkey = Signal()
    _gesture = Signal()
    _escape = Signal()
    _enter = Signal()
    _inserted = Signal()
    _notify = Signal(str)  # уведомление в трее из фонового потока

    def __init__(self, app: QApplication, cfg: Config):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.state = "idle"  # idle | recording | finishing | editing | inserting
        self.target = 0

        self.engine = Engine(cfg)
        self.theme = with_glow(get_theme(cfg.theme), cfg.glow)
        self.island = Island(self.engine, self.theme)
        self.editor = Editor(self.engine, cfg.enter_confirms, self.theme)

        self.engine.text_changed.connect(self.on_text)
        self.engine.dictation_done.connect(self.on_dictation_done)
        self.engine.dictation_cancelled.connect(self.cancel_recording)
        self.engine.dictation_idle.connect(self.on_dictation_idle)
        self.engine.command_armed.connect(self.on_command_armed)
        self.engine.command.connect(self.on_voice_command)
        self.engine.model_state.connect(self.on_model_state)
        self.engine.mic_error.connect(self.on_mic_error)
        self.engine.mic_live.connect(self.on_mic_live)
        self._session = 0
        self.editor.confirmed.connect(self.on_confirm)
        self.editor.cancelled.connect(self.on_editor_cancel)

        self._hotkey.connect(self.on_hotkey)
        self._gesture.connect(lambda: self.on_hotkey("жест мыши"))
        self._escape.connect(self.cancel_recording)
        self._enter.connect(self.finish_recording)
        self._inserted.connect(self._back_to_idle)
        self._notify.connect(lambda message: self.tray.showMessage("Голосовой ввод", message))

        self.hook = KeyboardHook(self._hotkey.emit, self._escape.emit, self._enter.emit, cfg.double_tap_ms)
        self.hook.start()
        # Хук мыши ставим, когда модель загружена: при загрузке GIL подолгу занят, и курсор бы подтормаживал.
        self.mouse = MouseGesture(self._gesture.emit, cfg.mouse_gesture_ms)

        self.tray = self._make_tray()

    # ----------------------------------------------------------------- трей

    def _make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(make_tray_icon(), self.app)
        tray.setToolTip("Голосовой ввод — загрузка модели…")
        menu = QMenu()
        start = QAction("Начать диктовку   (\\ \\)", menu)
        start.triggered.connect(lambda: QTimer.singleShot(150, self.on_hotkey))
        menu.addAction(start)
        style_menu = menu.addMenu("Стиль")
        group = QActionGroup(style_menu)
        for theme in THEMES.values():
            action = QAction(theme.title, style_menu, checkable=True)
            action.setChecked(theme.key == self.theme.key)
            action.triggered.connect(lambda _=False, key=theme.key: self.set_theme(key))
            group.addAction(action)
            style_menu.addAction(action)
        self.glow_menu = menu.addMenu("Свечение")
        glow_group = QActionGroup(self.glow_menu)
        self._glow_actions: dict[str, QAction] = {}
        for key, (title, _) in GLOWS.items():
            action = QAction(title, self.glow_menu, checkable=True)
            action.triggered.connect(lambda _=False, key=key: self.set_glow(key))
            glow_group.addAction(action)
            self.glow_menu.addAction(action)
            self._glow_actions[key] = action
        menu.addSeparator()
        gesture = QAction("Жест мышью: ЛКМ → ПКМ → ЛКМ", menu, checkable=True)
        gesture.setChecked(self.cfg.mouse_gesture)
        gesture.triggered.connect(self.set_mouse_gesture)
        menu.addAction(gesture)
        self.autostart_action = QAction("Запускать вместе с Windows", menu, checkable=True)
        # triggered, а не toggled: setChecked при открытии меню не должен трогать реестр.
        self.autostart_action.triggered.connect(set_autostart)
        menu.aboutToShow.connect(self._refresh_menu)
        menu.addAction(self.autostart_action)
        settings = QAction("Настройки (config.json)", menu)
        settings.triggered.connect(lambda: open_in_notepad(CONFIG_PATH))
        menu.addAction(settings)
        log_action = QAction("Открыть лог", menu)
        log_action.triggered.connect(lambda: open_in_notepad(CONFIG_PATH.parent / "voice.log"))
        menu.addAction(log_action)
        menu.addSeparator()
        quit_action = QAction("Выход", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        self._menu = menu
        tray.show()
        return tray

    def on_model_state(self, state: str) -> None:
        if state == "ready":
            self.tray.setToolTip("Голосовой ввод — дважды нажмите «\\»")
            if self.cfg.mouse_gesture:
                self.mouse.start()
            if self.state == "recording":
                self.island.set_status("listening" if self.engine.live else "starting")
        elif state == "error":
            self.tray.setToolTip("Голосовой ввод — ошибка загрузки модели")
            self.tray.showMessage("Голосовой ввод", "Не удалось загрузить модель. Подробности в логе.")

    def _refresh_menu(self) -> None:
        self.autostart_action.setChecked(autostart_enabled())
        # Цвет свечения есть только у стилей с подсветкой.
        self.glow_menu.setEnabled(self.theme.glow)
        current = glow_key(self.theme)
        for key, action in self._glow_actions.items():
            action.setChecked(key == current)

    def set_theme(self, key: str) -> None:
        self.cfg.theme = get_theme(key).key
        self._apply_theme()

    def set_glow(self, key: str) -> None:
        self.cfg.glow = key
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.cfg.save()
        self.theme = with_glow(get_theme(self.cfg.theme), self.cfg.glow)
        self.island.set_theme(self.theme)
        self.editor.apply_theme(self.theme)
        if self.state == "idle":
            # Показываем, как выглядит стиль: остров на пару секунд с примером текста.
            QTimer.singleShot(250, self._preview_theme)

    def set_mouse_gesture(self, on: bool) -> None:
        self.cfg.mouse_gesture = on
        self.cfg.save()
        if not on:
            self.mouse.stop()
        elif self.engine.ready:
            self.mouse.start()

    def _preview_theme(self) -> None:
        if self.state != "idle":
            return
        if self.island.isVisible():
            # Прошлое демо ещё на экране — убираем и даём экрану перерисоваться, иначе оно попадёт в снимок стекла.
            self.island.dismiss(animated=False)
            QTimer.singleShot(80, self._preview_theme)
            return
        self._capture_backdrop(0)
        self.island.present(loading=False, hwnd=0, demo=True)

    def _capture_backdrop(self, hwnd: int) -> None:
        if self.theme.glass:
            Backdrop.capture(backdrop_area(hwnd), self.theme.blur)

    def on_mic_error(self, message: str) -> None:
        self.tray.showMessage("Голосовой ввод", f"Не удалось открыть микрофон: {message}")

    def quit(self) -> None:
        self.engine.stop()
        self.hook.stop()
        self.mouse.stop()
        self.tray.hide()
        self.app.quit()

    # ----------------------------------------------------------------- запись

    def _our_windows(self) -> set[int]:
        return {int(self.island.winId()), int(self.editor.winId())}

    def on_hotkey(self, source: str = "хоткей") -> None:
        log.info("%s: состояние %s, активное окно %s", source, self.state, win32.describe_window(win32.foreground_window()))
        if self.state == "idle":
            self.start_recording()
        elif self.state == "recording":
            self.finish_recording()

    def start_recording(self) -> None:
        hwnd = win32.foreground_window()
        if hwnd in self._our_windows():
            return
        # Рабочий стол / панель задач: окна-цели нет — покажемся у курсора, а текст отдадим в буфер.
        self.target = 0 if win32.is_shell_window(hwnd) else hwnd
        if not self.engine.begin_dictation():
            return
        self.state = "recording"
        self.hook.capture_keys = True
        self.island.hide()  # если показывалось демо стиля
        self._capture_backdrop(self.target)
        if not self.engine.ready:
            status = "loading"
        else:
            # «Слушаю» — только когда микрофон реально заработал, чтобы первые слова не терялись.
            status = "listening" if self.engine.live else "starting"
        self.island.present(loading=not self.engine.ready, hwnd=self.target, status=status)
        self._session += 1
        QTimer.singleShot(2500, lambda s=self._session: self._mic_fallback(s))

    def on_mic_live(self) -> None:
        if self.state == "recording" and self.island.status == "starting":
            self.island.set_status("listening")

    def _mic_fallback(self, session: int) -> None:
        # Некоторые гарнитуры в тишине отдают ровные нули — не держим «Подготовку» вечно.
        if session == self._session and self.state == "recording" and self.island.status == "starting":
            log.warning("микрофон 2,5 с отдаёт тишину — показываю «Слушаю»")
            self.island.set_status("listening")

    def on_text(self, text: str) -> None:
        if self.state == "recording":
            self.island.set_text(text)

    def finish_recording(self) -> None:
        if self.state != "recording":
            return
        self.state = "finishing"
        self.island.set_status("processing")
        self.engine.finish_dictation()

    def cancel_recording(self) -> None:
        if self.state not in ("recording", "finishing"):
            return
        log.info("запись отменена")
        self.hook.capture_keys = False
        self.engine.stop()
        self.island.dismiss()
        self.state = "idle"

    def on_command_armed(self, command: str) -> None:
        if self.state == "recording":
            self.island.set_armed(command)

    def on_dictation_idle(self, text: str) -> None:
        """Долго молчали — запись остановлена сама. Микрофон не держим, текст не теряем."""
        if self.state not in ("recording", "finishing"):
            return
        self.hook.capture_keys = False
        self.engine.stop()
        self.island.dismiss()
        self.state = "idle"
        if text:
            win32.clipboard_set_text(text, temporary=False)
            self.tray.showMessage(
                "Голосовой ввод",
                f"{self.cfg.auto_stop_s} с тишины — запись остановлена, текст в буфере обмена (Ctrl+V).",
            )

    def on_dictation_done(self, text: str, send: bool) -> None:
        if self.state not in ("recording", "finishing"):
            return
        self.hook.capture_keys = False
        if send:
            # «…отправить» прямо во время записи — без редактора.
            self.island.dismiss()
            self._insert(text, send=True)
            return
        self.mouse.enabled = False  # в редакторе клики работают как обычно
        self.hook.enabled = False  # в редакторе «\\» печатается как обычно
        self.state = "editing"
        rect = self.island.card_rect_global()
        self.island.dismiss(animated=False)
        self.editor.open(text, from_rect=rect, hwnd=self.target)
        self.engine.begin_commands()

    # ----------------------------------------------------------------- редактор

    def on_voice_command(self, command: str) -> None:
        if self.state != "editing":
            return
        if command == "ok":
            self.editor.confirm(send=False)
        elif command == "send":
            self.editor.confirm(send=True)
        elif command == "cancel":
            self.editor.cancel()

    def on_confirm(self, text: str, send: bool) -> None:
        if self.state == "editing":
            self._insert(text, send)

    def _insert(self, text: str, send: bool) -> None:
        self.state = "inserting"
        target, method = self.target, self.cfg.paste_method
        if target:
            # Из редактора: он ещё активен (прячется после этого сигнала) — сейчас Windows точно даст отдать фокус.
            # Во время записи фокус и так в исходном окне.
            win32.force_foreground(target)
        self.engine.stop()
        send_key = self.cfg.send_key if send else None
        if not target:
            log.info("поля ввода нет — копирую %d символов в буфер", len(text))
            if text:
                win32.clipboard_set_text(text, temporary=False)
                self.tray.showMessage("Голосовой ввод", "Поля ввода не было — текст скопирован в буфер обмена (Ctrl+V).")
            self._back_to_idle()
            return
        keep = self.cfg.keep_in_clipboard

        def work() -> None:
            try:
                ok = inserter.insert_text(target, text, method, send_key, keep)
            except Exception:
                log.exception("ошибка вставки")
                ok = False
            if not ok and text:
                # Не вставили (и, главное, не отправили) — текст не должен потеряться.
                win32.clipboard_set_text(text, temporary=False)
                self._notify.emit("Не получилось вернуться в исходное окно — текст скопирован в буфер обмена (Ctrl+V).")
            self._inserted.emit()

        threading.Thread(target=work, name="insert", daemon=True).start()

    def on_editor_cancel(self) -> None:
        if self.state != "editing":
            return
        log.info("редактор: отмена")
        self.state = "inserting"
        target = self.target
        if target:
            win32.force_foreground(target)  # пока редактор активен — см. on_confirm
        self.engine.stop()

        def work() -> None:
            if target:
                inserter.return_focus(target)
            self._inserted.emit()

        threading.Thread(target=work, name="refocus", daemon=True).start()

    def _back_to_idle(self) -> None:
        self.hook.enabled = True
        self.mouse.enabled = True
        self.state = "idle"


# --------------------------------------------------------------------------- автозапуск


def _launch_command() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = Path(__file__).resolve().parents[2] / "run.pyw"
    return f'"{pythonw}" "{script}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def open_in_notepad(path: Path) -> None:
    subprocess.Popen(["notepad.exe", str(path)])
