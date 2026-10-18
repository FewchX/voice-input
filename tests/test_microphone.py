"""Выбор микрофона: Bluetooth-наушники не берём, чтобы они не уходили в телефонный режим."""

from voice_input.engine import pick_non_bluetooth

MME, DS, WASAPI, WDMKS = 0, 1, 2, 3


def dev(name, api, inputs=2):
    return {"name": name, "hostapi": api, "max_input_channels": inputs}


# Как на этом ноутбуке, когда подключены Bose (имена MME обрезаны до 31 символа).
DEVICES = [
    dev("Переназначение звуковых устр. - Input", MME),
    dev("Головной телефон (LE-Bose QC Ul", MME, 1),
    dev("Микрофон (Realtek High Definiti", MME),
    dev("Динамики (Realtek)", MME, 0),
    dev("Микрофон (Realtek High Definition Audio)", WASAPI),
    dev("Стерео микшер (Realtek HD Audio Stereo input)", WDMKS),
    dev("Головной телефон (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\n;(LE-Bose QC Ultra Headphones))", WDMKS, 1),
]


def test_bluetooth_default_replaced_by_builtin():
    assert pick_non_bluetooth(DEVICES, 1) == 2


def test_english_windows_names():
    devices = [dev("Microsoft Sound Mapper - Input", MME), dev("Headset (LE-Bose QC Ultra He", MME, 1),
               dev("Microphone (Realtek High Defini", MME), DEVICES[-1]]
    assert pick_non_bluetooth(devices, 1) == 2


def test_builtin_default_kept():
    assert pick_non_bluetooth(DEVICES, 2) == 2


def test_only_bluetooth_mic_is_used_anyway():
    devices = [DEVICES[0], DEVICES[1], DEVICES[-1]]
    assert pick_non_bluetooth(devices, 1) == 1
