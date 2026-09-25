from __future__ import annotations

import re
from dataclasses import dataclass


_WORD_RE = re.compile(r"[A-Za-z]+|[А-Яа-яЁёІіЇїЄєҐґ]+", re.UNICODE)
_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]", re.UNICODE)
_UKRAINIAN_WORDS = {
    "агентів", "використовував", "використовувала", "використовували",
    "власні", "власний", "власна", "обирали", "вибір", "зображень",
    "зображення", "генерації", "готовий", "готова", "готове", "неоплачуваного",
    "працюватиму", "підкажіть", "наразі", "залежно", "оскільки", "заразом",
    "будьласка", "дякую", "вітаю", "співбесіда", "заробітну", "заробітна",
    "запитання", "відповідь", "потрібно", "потрібен", "можливості", "цікавить",
    "проєкт", "проєкти", "досвід", "комерційного", "залучення", "долучитися",
    "доєднатися", "зручно", "погоджуюся", "підходить", "вважаю", "зазвичай",
    "ви", "ваші", "ваша", "ваше", "хочете", "можете", "можемо", "будемо",
    "працювали", "працює", "працюю", "маю", "маєте", "розглянути", "розглядаю",
}
_ENGLISH_MARKERS = {
    "a", "am", "an", "and", "are", "available", "can", "could", "for", "from",
    "great", "have", "hello", "how", "i", "immediately", "is", "it", "me", "my",
    "no", "noted", "of", "okay", "please", "project", "ready", "salary", "sure",
    "thanks", "thank", "the", "to", "we", "welcome", "what", "with", "worked",
    "yes", "you", "your", "hi", "tell", "use", "uses", "using", "work", "about",
    "good", "sounds", "fine", "perfect", "got", "understand", "understood", "makes", "sense",
    "experience", "experienced", "years", "months", "start", "join", "joining",
    "could", "range", "expect", "expectations", "rate", "salary", "availability",
}


@dataclass(frozen=True)
class ReplyLanguageCheck:
    expected: str
    passed: bool
    checklist: str


def expected_reply_language(incoming: str) -> str:
    words = [token.casefold() for token in _WORD_RE.findall(incoming)]
    latin = sum(character.isascii() and character.isalpha() for character in incoming)
    cyrillic = sum("\u0400" <= character <= "\u052f" and character.isalpha() for character in incoming)
    cyrillic_words = sum(any("\u0400" <= char <= "\u052f" for char in word) for word in words)
    if cyrillic_words >= 2 or (cyrillic_words and cyrillic / max(1, latin + cyrillic) >= 0.2):
        return "Russian"
    if set(words) & _ENGLISH_MARKERS:
        return "English"
    return "English" if latin > cyrillic else "Russian"


def _contains_ukrainian(reply: str, tokens: set[str]) -> bool:
    return (
        any(character.casefold() in {"ї", "є", "ґ"} for character in reply)
        or sum(character.casefold() == "і" for character in reply) > 0
        or bool(tokens & _UKRAINIAN_WORDS)
    )


def check_reply_language(reply: str, incoming: str) -> ReplyLanguageCheck:
    expected = expected_reply_language(incoming)
    letters = _LETTER_RE.findall(reply)
    if not letters:
        return ReplyLanguageCheck(expected, False, f"[ ] Reply language is {expected}")

    latin = sum(character.isascii() and character.isalpha() for character in letters)
    cyrillic = len(letters) - latin
    tokens = {token.casefold() for token in _WORD_RE.findall(reply)}
    ukrainian = _contains_ukrainian(reply, tokens)
    if expected == "English":
        passed = (
            latin / len(letters) >= 0.65
            and bool(tokens & _ENGLISH_MARKERS)
            and not ukrainian
        )
    else:
        passed = cyrillic / len(letters) >= 0.55 and not ukrainian
    state = "[x]" if passed else "[ ]"
    return ReplyLanguageCheck(expected, passed, f"{state} Reply is in {expected}; Ukrainian is not used")
