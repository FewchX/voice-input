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


# --------------------------------------------------------------------------- чистка текста

# «а-а», «э-э-э», «м-м», «ммм», «эм» — паразиты. Одиночные «а», «э» не трогаем: «а» — обычный союз.
_FILLER = re.compile(r"(?<![\w-])(?:([аэм])(?:-?\1)+|эм+)(?![\w-])[,.!?…]*\s*", re.IGNORECASE)
# um, umm, uh, uhm, erm, hmm, mmm. «mm» не трогаем: это ещё и миллиметры.
_FILLER_EN = re.compile(r"(?<![\w-])(?:u+h+m*|u+m+|e+r+m+|h+m+|m{3,})(?![\w-])[,.!?…]*\s*", re.IGNORECASE)
_CAP = "\x00"  # метка: следующее слово начинает предложение


def remove_fillers(text: str, language: str = "ru") -> str:
    """«А-а я бы хотел, э-э, попробовать» → «Я бы хотел, попробовать»; «Um, I think, uh, yes» → «I think, yes»."""

    def drop(m: re.Match) -> str:
        return _CAP if m.group(0)[0].isupper() else ""

    text = (_FILLER_EN if language == "en" else _FILLER).sub(drop, text)
    text = re.sub(_CAP + r"\s*(\W*)(\w)", lambda m: m.group(1) + m.group(2).upper(), text).replace(_CAP, "")
    text = re.sub(r"\s+([,.!?…])", r"\1", text)  # «слово ,» → «слово,»
    text = re.sub(r",\s*([.!?…])", r"\1", text)  # «слово, .» → «слово.»
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.lstrip(",;:—– ").rstrip(",;:—– ")


_PATTERNS: dict[tuple, list[tuple[re.Pattern, str]]] = {}


def _replacement_patterns(replacements: dict[str, str]) -> list[tuple[re.Pattern, str]]:
    key = tuple(replacements.items())
    if key not in _PATTERNS:
        out = []
        # Сначала длинные фразы: «клод код» раньше, чем «клод».
        for src in sorted(replacements, key=lambda s: (-len(s.split()), -len(s))):
            words = ["".join("[её]" if c in "её" else c for c in re.escape(w)) for w in src.lower().split()]
            if words:
                rx = re.compile(r"(?<![\w-])" + r"[\s-]+".join(words) + r"(?![\w-])", re.IGNORECASE)
                out.append((rx, replacements[src]))
        _PATTERNS[key] = out
    return _PATTERNS[key]


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    """Словарь замен: «гид статус» → «git статус», «кладкод» → «Claude Code». Регистр и ё/е не важны."""

    def put(value: str):
        def sub(m: re.Match) -> str:
            # В начале предложения «Гид…» → «Git…», а не «git…».
            return value[:1].upper() + value[1:] if m.group(0)[:1].isupper() and value[:1].islower() else value

        return sub

    for rx, value in _replacement_patterns(replacements):
        text = rx.sub(put(value), text)
    return text


def clean(text: str, replacements: dict[str, str], fillers: bool = True, language: str = "ru") -> str:
    """Финальная чистка распознанного текста перед показом и вставкой."""
    if fillers:
        text = remove_fillers(text, language)
    return apply_replacements(text, replacements) if replacements else text


def display_word(words: list[str]) -> str:
    """Как показать команду в подсказке: первое слово списка, без «~»."""
    return words[0].removeprefix(FUZZY) if words else ""


def join_text(left: str, right: str) -> str:
    left, right = left.strip(), right.strip()
    if not left or not right:
        return left or right
    return f"{left} {right}"
