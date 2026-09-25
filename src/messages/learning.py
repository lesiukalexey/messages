from __future__ import annotations

import fcntl
import os
from pathlib import Path

import yaml


LOCK_PATH = Path("/home/admin/messages-runtime/learned-answers.lock")


def save_learned_answer(
    path: Path, question: str, answer: str, lock_path: Path = LOCK_PATH
) -> None:
    question = " ".join(question.split())[:500]
    answer = answer.strip()[:5000]
    if not question or not answer:
        raise ValueError("A question and answer are required")

    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.parent.chmod(0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with path.open("r+", encoding="utf-8") as profile:
            document = yaml.safe_load(profile) or {}
            if not isinstance(document, dict):
                raise ValueError("The profile YAML root must be a mapping")
            learned = document.setdefault("learned_answers", {})
            if not isinstance(learned, dict):
                raise ValueError("learned_answers must be a YAML mapping")
            learned[question] = answer
            rendered = yaml.safe_dump(
                document,
                allow_unicode=True,
                sort_keys=False,
                width=100,
                default_flow_style=False,
            )
            profile.seek(0)
            profile.write(rendered)
            profile.truncate()
            profile.flush()
            os.fsync(profile.fileno())
