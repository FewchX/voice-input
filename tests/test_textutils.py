import pytest

from voice_input import textutils as t
from voice_input.config import DEFAULT_REPLACEMENTS, Config

cfg = Config()


# --------------------------------------------------------------------------- команды


def test_stop_is_stripped_with_quotes():
    assert t.strip_trailing_word('А потом я скажу:"Стоп".', cfg.stop_words) == ("А потом я скажу", True)
    assert t.strip_trailing_word("Привет, как дела? Стоп.", cfg.stop_words) == ("Привет, как дела?", True)
    assert t.strip_trailing_word("Привет. Stop.", cfg.stop_words) == ("Привет.", True)


@pytest.mark.parametrize(
    "text, words, expected",
    [
        ("Ну, вроде всё. Можно и отправить.", "send_words", True),
        ("Да, всё верно. О'кей.", "ok_words", True),
        ("ну ладно о кей", "ok_words", True),
        ("Надо отправить письмо.", "send_words", False),
        ("", "send_words", False),
        ("Он сам отправит.", "send_words", False),  # опечатки — только для слов с «~»
    ],
)
def test_ends_with(text, words, expected):
    assert t.ends_with(text, getattr(cfg, words)) is expected


@pytest.mark.parametrize(
    "word",
    ["Энтер.", "энтер", "Ентер!", "Enter.", "ENTER", "Интер.", "Энтр.", "энтэр", "inter", "entr", "Энджер.", "Anjer."],
)
def test_enter_spellings_send(word):
    assert t.ends_with(f"Ну всё, договорились. {word}", cfg.send_words)


@pytest.mark.parametrize(
    "word", ["Центр.", "Интернет.", "Тенор.", "Менеджер.", "Выиграли тендер.", "Ментор.", "Интерьер.", "Ну ладно."]
)
def test_similar_words_do_not_send(word):
    assert not t.ends_with(f"Ну всё, договорились. {word}", cfg.send_words)


def test_enter_is_stripped():
    assert t.strip_trailing_word("Встретимся в субботу. Энтер.", cfg.send_words) == ("Встретимся в субботу.", True)
    assert t.strip_trailing_word("Встретимся в субботу, Enter", cfg.send_words) == ("Встретимся в субботу", True)


def test_cancel_only_as_whole_phrase():
    assert t.is_command("Отмена.", cfg.cancel_words)
    assert not t.is_command("Нажми кнопку отмена.", cfg.cancel_words)


# --------------------------------------------------------------------------- чистка


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("А-а я бы хотел это упростить.", "Я бы хотел это упростить."),
        ("Э-э, мне всё нравится.", "Мне всё нравится."),
        ("Поставь э-э на 20 секунд.", "Поставь на 20 секунд."),
        ("Не нужна мышка, э-э, вообще.", "Не нужна мышка, вообще."),
        ("Это хорошо. Э-э.", "Это хорошо."),
        ("Э-э.", ""),
        ("А мы пошли в кино.", "А мы пошли в кино."),  # одиночное «а» — союз
        ("Ммм, вкусно.", "Вкусно."),
    ],
)
def test_remove_fillers(raw, clean):
    assert t.remove_fillers(raw) == clean


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("Закоммить изменения в гид и сделай пуш.", "Закоммить изменения в git и сделай пуш."),
        ("Гид статус покажи.", "Git статус покажи."),
        ("Спроси у клада, как лучше.", "Спроси у Claude, как лучше."),
        ("Открой кладкод в терминале.", "Открой Claude Code в терминале."),
        ("А Клот Код открыт.", "А Claude Code открыт."),
        ("Слово clod он пишет как клот.", "Слово Claude он пишет как Claude."),
        ("Проверь пол реквест.", "Проверь пулл-реквест."),
        ("Сделай мертж в мейн.", "Сделай мёрж в мейн."),
        ("Залей на гитхаб.", "Залей на GitHub."),
        ("Наш гид-экскурсовод был хорош.", "Наш гид-экскурсовод был хорош."),  # часть слова не трогаем
        ("Мы пошли в кладовку.", "Мы пошли в кладовку."),
    ],
)
def test_replacements(raw, clean):
    assert t.apply_replacements(raw, DEFAULT_REPLACEMENTS) == clean


def test_clean_combines_both():
    assert t.clean("Э-э, закинь это в гид.", DEFAULT_REPLACEMENTS) == "Закинь это в git."
    assert t.clean("Э-э, закинь это в гид.", {}, fillers=False) == "Э-э, закинь это в гид."


# --------------------------------------------------------------------------- английский

en = Config().lang("en")


@pytest.mark.parametrize(
    "text, words, expected",
    [
        ("Please merge it today. Send.", "send_words", True),
        ("Let me know when you are ready. Enter.", "send_words", True),
        ("Ready. Inter.", "send_words", True),  # «~enter» узнаётся и с одной ошибкой
        ("I will send you the file tomorrow.", "send_words", False),
        ("Okay.", "ok_words", True),
        ("We are done.", "ok_words", True),
    ],
)
def test_english_commands(text, words, expected):
    assert t.ends_with(text, getattr(en, words)) is expected


def test_english_stop_and_cancel():
    assert t.strip_trailing_word("Hello, this is a test. Stop.", en.stop_words) == ("Hello, this is a test.", True)
    assert t.is_command("Cancel.", en.cancel_words)
    assert not t.is_command("Cancel the meeting.", en.cancel_words)


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("Um, I think, uh, we should ship it.", "I think, we should ship it."),
        ("Hmm. Let me check.", "Let me check."),
        ("It is 5 mm wide.", "It is 5 mm wide."),
        ("The umbrella and the hummingbird.", "The umbrella and the hummingbird."),
    ],
)
def test_english_fillers(raw, clean):
    assert t.clean(raw, {}, language="en") == clean


def test_russian_fillers_untouched_in_english():
    assert t.remove_fillers("Э-э, hello", "en") == "Э-э, hello"


def test_display_word():
    assert t.display_word(cfg.stop_words) == "стоп"
    assert t.display_word(en.send_words) == "send"
    assert t.display_word(["~энтер"]) == "энтер"
