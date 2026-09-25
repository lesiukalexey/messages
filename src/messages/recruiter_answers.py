from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


TOKEN_RE = re.compile(r"[a-z0-9]+|[а-яёіїєґ]+", re.IGNORECASE)
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "can", "could", "did", "do",
    "does", "for", "from", "have", "how", "i", "if", "in", "is", "it", "me",
    "my", "of", "on", "or", "please", "tell", "that", "the", "their", "there",
    "they", "this", "to", "what", "when", "where", "which", "who", "why", "with",
    "would", "you", "your", "у", "в", "на", "и", "или", "а", "что", "как", "когда",
    "где", "какой", "какая", "какие", "ли", "есть", "мне", "ты", "вы", "твой", "ваш",
    "расскажи", "расскажите", "рассказать", "пожалуйста", "можешь", "можете",
    "могли", "бы", "про", "для", "по", "из", "вас", "какие", "какой", "какая",
}
SYNONYM_GROUPS = (
    {
        "salary", "compensation", "pay", "wage", "remuneration", "зарплата",
        "зарплатная", "зарплатные", "зарплатных", "зарплатную", "зарплате",
        "зарплату", "зарплаты", "зарплатой", "оклад", "компенсация", "доход",
        "зп", "вилка", "вилки", "вилку",
    },
    {"expectation", "expectations", "expected", "ожидание", "ожидания"},
    {"experience", "experienced", "экспертиза", "опыт", "стаж"},
    {"year", "years", "год", "года", "лет"},
    {"location", "located", "based", "country", "город", "страна", "локация", "находиться", "находишься", "находитесь", "живу", "живешь", "живёшь", "проживаешь"},
    {
        "start", "starting", "available", "availability", "notice",
        "time", "timeframe", "join", "joining", "начать", "приступить",
        "доступность", "срок", "час", "часу",
        "приєднатись", "приєднатися", "доєднатись", "доєднатися",
        "долучитись", "долучитися",
    },
    {"english", "английский", "английского", "английским", "английском"},
    {"level", "proficiency", "fluent", "уровень", "владение"},
    {"build", "built", "building", "разработать", "создавать", "создал", "строить"},
    {"production", "продакшн", "продуктивный"},
    {"team", "people", "management", "руководство", "управление", "команда", "людьми"},
    {"remote", "relocate", "relocation", "переезд", "удаленно", "удалённо"},
)
CANONICAL: dict[str, str] = {}
for group_index, group in enumerate(SYNONYM_GROUPS):
    for token in group:
        CANONICAL[token] = f"syn{group_index}"


def _tokens(text: str) -> set[str]:
    normalized = text.casefold().replace("_", " ").replace("-", " ")
    result: set[str] = set()
    for token in TOKEN_RE.findall(normalized):
        token = CANONICAL.get(token, token)
        if token in STOP_WORDS or token.startswith("syn") and token in STOP_WORDS:
            continue
        # English plurals and common Russian case endings are normalized conservatively.
        if token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4 and token not in CANONICAL:
            token = token[:-1]
        result.add(token)
    return result


class RecruiterAnswers:
    """Select a few approved Job Apply facts relevant to one recruiter message."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime_ns: int | None = None
        self._entries: list[tuple[str, str, set[str]]] = []

    def _load(self) -> None:
        stat = self.path.stat()
        if self._mtime_ns == stat.st_mtime_ns:
            return
        document: Any = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        values = document.get("values", {}) if isinstance(document, dict) else {}
        entries: list[tuple[str, str, set[str]]] = []
        if not isinstance(values, dict):
            values = {}

        def field_answer(field: str, value: Any) -> Any:
            if field != "salary_expectation":
                return value
            try:
                amount = f"{int(float(value)):,}"
            except (TypeError, ValueError):
                amount = str(value)
            currency = str(values.get("currency", "")).strip()
            pay_period = str(values.get("pay_period", "")).strip().casefold()
            period = {"monthly": "per month", "hourly": "per hour", "yearly": "per year"}.get(pay_period)
            parts = [currency, amount, "gross"]
            if period:
                parts.append(period)
            return " ".join(part for part in parts if part)

        def add_entry(question: Any, answer_value: Any) -> None:
            if not isinstance(question, str) or not isinstance(answer_value, (str, int, float)):
                return
            question_text = question.replace("_", " ").strip()
            if any(
                marker in question.casefold()
                for marker in ("privacy", "consent", "agree", "certify", "security_code", "i_confirm")
            ):
                return
            answer = str(answer_value).strip()
            question_tokens = _tokens(question_text)
            if answer and question_tokens:
                entries.append((question_text, answer, question_tokens))

        for key, value in values.items():
            if key in {"currency", "pay_period", "date_available"}:
                continue
            add_entry(key, field_answer(key, value))

        aliases = document.get("aliases", {}) if isinstance(document, dict) else {}
        if isinstance(aliases, dict):
            for question, target in aliases.items():
                if (
                    isinstance(target, str)
                    and target in values
                    and target not in {"currency", "pay_period", "date_available"}
                ):
                    add_entry(question, field_answer(target, values[target]))

        learned_answers = document.get("learned_answers", {}) if isinstance(document, dict) else {}
        if isinstance(learned_answers, dict):
            for question, answer in learned_answers.items():
                if (
                    isinstance(question, str)
                    and "salary" in question.casefold()
                    and str(answer).strip() == str(values.get("salary_expectation", "")).strip()
                ):
                    continue
                add_entry(question, answer)
        self._entries = entries
        self._mtime_ns = stat.st_mtime_ns

    def match(self, message: str, limit: int = 4) -> list[dict[str, str]]:
        try:
            self._load()
        except (OSError, UnicodeError, yaml.YAMLError):
            return []
        query_tokens = _tokens(message)
        if not query_tokens:
            return []
        matches: list[tuple[float, int, str, str]] = []
        for question, answer, key_tokens in self._entries:
            shared = query_tokens & key_tokens
            if not shared:
                continue
            query_coverage = len(shared) / len(query_tokens)
            # Avoid broad one-word matches unless the incoming question itself is short.
            if len(query_tokens) == 1:
                if len(key_tokens) > 3:
                    continue
            elif len(shared) < 2 and (
                len(key_tokens) > 2 or not shared.intersection({"syn0", "syn5"})
            ):
                continue
            key_coverage = len(shared) / len(key_tokens)
            if query_coverage < 0.15 and key_coverage < 0.25:
                continue
            score = query_coverage * 0.75 + key_coverage * 0.25
            matches.append((score, len(shared), question, answer))
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected: list[dict[str, str]] = []
        seen_answers: set[str] = set()
        for _, _, question, answer in matches:
            if answer in seen_answers:
                continue
            selected.append({"question": question, "answer": answer})
            seen_answers.add(answer)
            if len(selected) >= limit:
                break

        if re.search(r"\baws\b|amazon\s+web\s+services", message, re.IGNORECASE):
            aws_answer = (
                "I have over 20 years of overall production backend experience. AWS is listed "
                "among my backend technologies, but my profile does not specify AWS-specific "
                "years, services, or responsibilities, so I cannot give those details accurately."
            )
            if aws_answer not in seen_answers and len(selected) < limit:
                selected.append({"question": "AWS commercial experience", "answer": aws_answer})
        return selected
