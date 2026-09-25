from __future__ import annotations

import fcntl
import os
import re
from pathlib import Path
from typing import Any, Iterable

import yaml


TOKEN_RE = re.compile(r"[a-z0-9]+|[а-яёіїєґ]+", re.IGNORECASE)
PROFILE_SPECIFIC_QUESTION = re.compile(
    r"\b(?:full name|first name|last name|email|e-mail|phone|linkedin|github|website|"
    r"telegram|location|address|city|country|state|postal code|salary|compensation|pay|rate|"
    r"application source|application motivation)\b|"
    r"имя|фамил|телефон|почт|зарплат|компенсац|ставк|локац|адрес|город|страна|"
    r"місто|заробіт|очікуван\w* оплат",
    re.IGNORECASE,
)
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
        "присоединиться", "присоединюсь",
        "подключиться", "подключусь", "выйти", "выход", "начинаю", "стартовать",
        "доступность", "срок", "час", "часу",
        "приєднатись", "приєднатися", "приєднаюсь", "доєднатись", "доєднатися",
        "долучитись", "долучитися",
    },
    {"english", "английский", "английского", "английским", "английском"},
    {"level", "proficiency", "fluent", "уровень", "владение"},
    {"build", "built", "building", "разработать", "создавать", "создал", "строить"},
    {"production", "продакшн", "продуктивный"},
    {"team", "people", "management", "руководство", "управление", "команда", "людьми"},
    {"remote", "relocate", "relocation", "переезд", "удаленно", "удалённо"},
)
def _read_profile_document(path: Path) -> Any:
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_SH)
        return yaml.safe_load(path.read_text(encoding="utf-8"))


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


def _same_answer_topic(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    shared = len(left_tokens & right_tokens)
    return left.casefold().strip() == right.casefold().strip() or (
        shared >= 2 and shared / max(len(left_tokens), len(right_tokens)) >= 0.66
    )


class RecruiterAnswers:
    """Select a few approved Job Apply facts relevant to one recruiter message."""

    def __init__(self, path: Path, persona_profile_paths: Iterable[Path] | None = None) -> None:
        self.path = path
        self.persona_profile_paths = tuple(
            dict.fromkeys([path, *(persona_profile_paths or ())])
        )
        self._mtime_ns: tuple[tuple[str, int], ...] | None = None
        self._entries: list[tuple[str, str, set[str]]] = []

    def _persona_documents(self) -> list[tuple[Path, dict[str, Any], int]]:
        try:
            primary = _read_profile_document(self.path)
        except (OSError, UnicodeError, yaml.YAMLError):
            return []
        if not isinstance(primary, dict):
            return []
        persona_id = str(primary.get("persona_id") or "").strip().casefold()
        paths = [self.path]
        if persona_id:
            paths.extend(path for path in self.persona_profile_paths if path != self.path)
        documents: list[tuple[Path, dict[str, Any], int]] = []
        for path in paths:
            try:
                document = primary if path == self.path else _read_profile_document(path)
                if not isinstance(document, dict):
                    continue
                source_persona = str(document.get("persona_id") or "").strip().casefold()
                if path != self.path and (not persona_id or source_persona != persona_id):
                    continue
                documents.append((path, document, path.stat().st_mtime_ns))
            except (OSError, UnicodeError, yaml.YAMLError):
                continue
        return documents

    def profile_persona_id(self) -> str:
        document = _read_profile_document(self.path)
        return (
            str(document.get("persona_id") or "").strip().casefold()
            if isinstance(document, dict)
            else ""
        )

    @staticmethod
    def _persona_answers(
        documents: list[tuple[Path, dict[str, Any], int]], primary_path: Path
    ) -> tuple[dict[str, str], dict[str, str]]:
        learned: dict[str, str] = {}
        owners: dict[str, str] = {}
        for path, document, _ in documents:
            for field, destination in (
                ("learned_answers", learned),
                ("owner_learned_answers", owners),
            ):
                answers = document.get(field, {})
                if not isinstance(answers, dict):
                    continue
                for question, answer in answers.items():
                    if not isinstance(question, str) or not isinstance(answer, str):
                        continue
                    if path != primary_path and PROFILE_SPECIFIC_QUESTION.search(question):
                        continue
                    key = question.strip()
                    value = answer.strip()
                    if not key or not value:
                        continue
                    if field == "owner_learned_answers":
                        for previous in list(owners):
                            if _same_answer_topic(previous, key):
                                owners.pop(previous)
                        destination[key] = value
                    else:
                        destination[key] = value
        learned = {
            question: answer
            for question, answer in learned.items()
            if not any(_same_answer_topic(question, owner) for owner in owners)
        }
        learned.update(owners)
        return learned, owners

    @staticmethod
    def _without_secrets(value: Any) -> Any:
        """Keep profile facts while excluding credentials and authentication data."""
        sensitive_markers = (
            "password", "passwd", "secret", "token", "api_key", "access_key",
            "private_key", "credential", "security_code", "login_code",
            "verification_code", "two_factor", "2fa", "otp", "passphrase",
            "api_id", "api_hash", "phone_number", "session_key",
        )
        if isinstance(value, dict):
            return {
                key: RecruiterAnswers._without_secrets(item)
                for key, item in value.items()
                if not any(marker in str(key).casefold() for marker in sensitive_markers)
            }
        if isinstance(value, list):
            return [RecruiterAnswers._without_secrets(item) for item in value]
        return value

    def profile_context(self) -> str:
        """Return the complete non-secret profile for semantic recruiter Q&A."""
        try:
            document: Any = _read_profile_document(self.path)
        except (OSError, UnicodeError, yaml.YAMLError):
            return ""
        if not isinstance(document, dict):
            return ""
        documents = self._persona_documents()
        learned, owners = self._persona_answers(documents, self.path)
        if learned:
            document["learned_answers"] = learned
        if owners:
            document["owner_learned_answers"] = owners
        return yaml.safe_dump(
            self._without_secrets(document),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()

    def for_recruiter_message(self, message: str) -> list[dict[str, str]]:
        """Provide semantic profile context and keyword matches for fallback."""
        context = self.profile_context()
        answers = self.match(message)
        if context:
            answers.insert(0, {
                "question": "Complete candidate profile context; normalize question to Russian and find relevant facts semantically across languages",
                "answer": context,
            })
        return answers

    def learned_answers_context(self) -> list[dict[str, str]]:
        """Share only owner-authored learned Q&A outside recruiter conversations."""
        try:
            document: Any = _read_profile_document(self.path)
        except (OSError, UnicodeError, yaml.YAMLError):
            return []
        learned = document.get("learned_answers", {}) if isinstance(document, dict) else {}
        if not isinstance(learned, dict):
            return []
        pairs = [
            {"question": question.strip(), "answer": answer.strip()}
            for question, answer in learned.items()
            if isinstance(question, str)
            and isinstance(answer, str)
            and question.strip()
            and answer.strip()
            and not any(
                marker in question.casefold()
                for marker in (
                    "password", "secret", "token", "credential", "security code", "2fa",
                )
            )
        ][-30:]
        return pairs

    def _load(self) -> None:
        documents = self._persona_documents()
        source_mtimes = tuple((str(path), mtime) for path, _, mtime in documents)
        if self._mtime_ns == source_mtimes:
            return
        primary = next((document for path, document, _ in documents if path == self.path), {})
        values = primary.get("values", {}) if isinstance(primary, dict) else {}
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

        aliases = primary.get("aliases", {}) if isinstance(primary, dict) else {}
        if isinstance(aliases, dict):
            for question, target in aliases.items():
                if (
                    isinstance(target, str)
                    and target in values
                    and target not in {"currency", "pay_period", "date_available"}
                ):
                    add_entry(question, field_answer(target, values[target]))

        learned_answers, _ = self._persona_answers(documents, self.path)
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
        self._mtime_ns = source_mtimes

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


class CategoryAnswers:
    """Read learned Q&A from one non-recruiter category file only."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            self.path.chmod(0o600)
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write("learned_answers: {}\n")

    def learned_answers_context(self) -> list[dict[str, str]]:
        try:
            document: Any = _read_profile_document(self.path)
        except (OSError, UnicodeError, yaml.YAMLError):
            return []
        learned = document.get("learned_answers", {}) if isinstance(document, dict) else {}
        if not isinstance(learned, dict):
            return []
        pairs = [
            {"question": question.strip(), "answer": answer.strip()}
            for question, answer in learned.items()
            if isinstance(question, str)
            and isinstance(answer, str)
            and question.strip()
            and answer.strip()
            and not any(
                marker in question.casefold()
                for marker in (
                    "password", "secret", "token", "credential", "security code", "2fa",
                )
            )
        ]
        return pairs[-30:]
