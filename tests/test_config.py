"""Настройки языка: русский — ключи верхнего уровня, английский — внутри english."""

from voice_input.config import ENGLISH_DEFAULTS, Config
from voice_input.engine import PINNED_MODELS


def test_russian_is_default():
    lang = Config().lang()
    assert lang.code == "ru" and lang.model == "gigaam-v3-e2e-rnnt" and "стоп" in lang.stop_words


def test_english_defaults_and_overrides():
    cfg = Config(language="en", english={"stop_words": ["halt"]})
    lang = cfg.lang()
    assert lang.code == "en" and lang.stop_words == ["halt"]
    assert lang.model == ENGLISH_DEFAULTS["model"] and lang.send_words == ENGLISH_DEFAULTS["send_words"]


def test_unknown_language_falls_back_to_russian():
    assert Config(language="de").lang().code == "ru"
    assert Config(language="en", english=None).lang().model == ENGLISH_DEFAULTS["model"]  # испорченный файл


def test_default_models_are_pinned():
    for code in ("ru", "en"):
        lang = Config().lang(code)
        assert (lang.model, lang.quantization) in PINNED_MODELS, code
