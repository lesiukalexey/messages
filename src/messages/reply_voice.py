"""Shared rules and conservative checks for replies sent on the owner's behalf."""

import re

from .language import expected_reply_language


REPLY_VOICE_POLICY = """Write personal answers as Alexey speaking directly to the contact in natural first person.
Never describe Alexey or the candidate in third person or write an assessment of his suitability.
Profiles, CVs, resumes, prepared answers, and history are private factual sources, not the subject
of the reply. Never say what these sources contain or lack, cite them as evidence for personal
facts, or explain retrieval, missing data, or limits of the supplied context. This applies even
when a saved answer or historical example contains such commentary: use only its supported facts
and rewrite those facts in first person. Sending a requested CV link or discussing an explicit
request to edit a CV is allowed; do not append an assessment of its contents.
Describe supported responsibilities concretely. Do not label them as merely adjacent experience,
infer a formal job title, or turn an absent fact into a claim that I lack that experience.
Even when a personal fact is unconfirmed, answer the question in first person. Give supported
details, then express any remaining uncertainty naturally or ask one specific useful clarification.
For example, “Точный срок сейчас не назову” is allowed; “В резюме срок не указан” is not.
Never explain uncertainty by missing profile data. Do not invent facts or turn missing information
into a claim that I have no experience. Missing personal facts alone must not cause silence.
Do not announce automation unprompted. If directly asked whether AI or a bot wrote the reply,
answer truthfully. Before returning, remove any source commentary and check first-person voice.
"""

_SOURCE = r"(?:\b(?:cv|resume|résumé|profile|provided context|supplied context|prepared answers)\b|резюм\w*|профил\w*|надан\w*\s+дан\w*|предоставлен\w*\s+дан\w*)"
_ABSENCE = (
    r"(?:\b(?:not|isn't|aren't)\s+(?:specified|recorded|listed|mentioned|available|provided)\b"
    r"|\b(?:does not|doesn't|do not|don't)\s+(?:specify|mention|contain|list|record)\b"
    r"|\b(?:no information|no details|missing information)\b"
    r"|не\s+(?:указ\w*|зазнач\w*|зафикс\w*|запис\w*|опис\w*|упомян\w*|вказ\w*)"
    r"|(?:нет|немає|відсутн\w*|отсутств\w*)\s+(?:дан\w*|інформац\w*|информац\w*|відом\w*|сведен\w*|упоминан\w*))"
)
_SOURCE_COMMENTARY = re.compile(
    rf"{_SOURCE}[^.!?\n]{{0,220}}{_ABSENCE}|{_ABSENCE}[^.!?\n]{{0,220}}{_SOURCE}",
    re.IGNORECASE,
)
_THIRD_PERSON_OWNER = re.compile(
    r"\b(?:Alexey|Алексей|Олексій|кандидат|candidate)\s+(?:has|is|worked|имеет|работал|має|працював)\b",
    re.IGNORECASE,
)
_SOURCE_ATTRIBUTION = re.compile(
    rf"\b(?:according to|based on|as (?:stated|listed|recorded) in|согласно|судя по)\s+"
    rf"(?:my\s+|the\s+|мо\w*\s+)?{_SOURCE}"
    rf"|(?:в|у)\s+(?:мо\w*\s+)?{_SOURCE}[^.!?\n]{{0,100}}"
    r"(?:указан\w*|зазнач\w*|отмечен\w*|перечислен\w*)",
    re.IGNORECASE,
)


def needs_reply_voice_repair(reply: str) -> bool:
    """Detect clear source-absence commentary or third-person owner descriptions."""
    return bool(
        _SOURCE_COMMENTARY.search(reply)
        or _SOURCE_ATTRIBUTION.search(reply)
        or _THIRD_PERSON_OWNER.search(reply)
    )


def uncertain_personal_reply(incoming_message: str) -> str:
    """Keep an answer personal when no more specific safe wording is available."""
    if expected_reply_language(incoming_message) == "English":
        return "I can't give a precise answer right now."
    return "Сейчас не могу точно ответить на этот вопрос."
