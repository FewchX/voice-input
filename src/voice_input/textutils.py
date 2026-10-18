"""Распознавание голосовых команд в тексте и чистка результата."""

from __future__ import annotations

import re

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
# Последнее слово + всё, что после него (пунктуация, кавычки, пробелы).
_LAST_WORD = re.compile(r"([\w'’-]+)[^\w]*$", re.UNICODE)

# «~энтер» в списке команд — узнавать слово и с одной ошибкой, и латиницей: Enter, ентер, интер, энтр…
FUZZY = "~"
# Латиница → кириллица по звучанию, «э» → «е»: так «Enter» и «энтер» сравниваются как одно слово.
_SOUND = str.maketrans(
    {
        **dict(zip("abdefgiklmnoprstuvz", "абдефгиклмнопрстувз")),
        "c": "к", "h": "х", "j": "дж", "q": "к", "w": "в", "x": "кс", "y": "и", "э": "е",
    }
)


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е").replace("’", "'")
    return " ".join(_PUNCT.sub(" ", text).split())


def _distance(a: str, b: str) -> int:
    """Расстояние Левенштейна (слова короткие — хватает простого варианта)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _targets(words: list[str]) -> list[tuple[list[str], bool]]:
    """Команды как (слова, нечёткое ли сравнение)."""
    out = []
    for w in words:
        fuzzy = w.startswith(FUZZY)
        tokens = normalize(w.removeprefix(FUZZY)).split()
        if tokens:
            out.append((tokens, fuzzy))
    return out


def _same(token: str, target: str, fuzzy: bool) -> bool:
    if token == target:
        return True
    return fuzzy and _distance(token.translate(_SOUND), target.translate(_SOUND)) <= 1


def _matches(tokens: list[str], target: list[str], fuzzy: bool) -> bool:
    return len(tokens) == len(target) and all(_same(t, w, fuzzy) for t, w in zip(tokens, target))


def strip_trailing_word(text: str, words: list[str]) -> tuple[str, bool]:
    """Если текст заканчивается одним из `words` («…, стоп.»), отрезает его."""
    m = _LAST_WORD.search(text)
    last = normalize(m.group(1)) if m else ""
    if not last or not any(_matches([last], target, fuzzy) for target, fuzzy in _targets(words)):
        return text, False
    # Вместе со словом убираем и то, что к нему липло: «скажу: "Стоп".» → «скажу».
    return text[: m.start()].rstrip(" \t\r\n,;:-—–\"'«„“([").rstrip(), True


def ends_with(text: str, words: list[str]) -> bool:
    """Фраза заканчивается одной из команд: «Ну, вроде всё. Отправить.», «…о кей»."""
    tokens = normalize(text).split()
    return any(_matches(tokens[-len(target) :], target, fuzzy) for target, fuzzy in _targets(words))


def is_command(text: str, words: list[str]) -> bool:
    """Фраза целиком состоит из команды: «Окей.», «ок, ок», «О'кей!»."""
    tokens = normalize(text).split()
    if not tokens:
        return False
    targets = _targets(words)
    if any(_matches(tokens, target, fuzzy) for target, fuzzy in targets):
        return True
    single = [(target, fuzzy) for target, fuzzy in targets if len(target) == 1]
    return all(any(_matches([t], target, fuzzy) for target, fuzzy in single) for t in tokens)


def join_text(left: str, right: str) -> str:
    left, right = left.strip(), right.strip()
    if not left or not right:
        return left or right
    return f"{left} {right}"
