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
