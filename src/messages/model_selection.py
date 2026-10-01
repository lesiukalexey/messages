from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ModelOption:
    model: str
    backend: str
    default_effort: str
    efforts: tuple[str, ...]


LIST1_MODEL_OPTIONS = (
    ModelOption("gpt-6-luna", "codex", "low", ("low", "medium", "high", "xhigh")),
    ModelOption("gpt-6-sol", "codex", "medium", ("low", "medium", "high", "xhigh")),
    ModelOption(
        "opencode/muse-spark-1.3-contributor-free",
        "opencode",
        "medium",
        ("low", "medium", "high"),
    ),
    ModelOption("opencode/big-pickle", "opencode", "medium", ("low", "medium", "high")),
    ModelOption("opencode/mimo-v2.6-flash-free", "opencode", "medium", ("low", "medium", "high")),
    ModelOption("opencode/nemotron-3.5-lightning-free", "opencode", "medium", ("low", "medium", "high")),
    ModelOption("opencode/ling-3.0-flash-fin-free", "opencode", "medium", ("low", "medium", "high")),
)

LIST1_BY_MODEL = {option.model: option for option in LIST1_MODEL_OPTIONS}
EFFORTS_BY_BACKEND = {
    "codex": ("low", "medium", "high", "xhigh"),
    "opencode": ("low", "medium", "high"),
}
BIO_DIRECTIVE_PREFIX = "model="
BIO_DIRECTIVE_RE = re.compile(
    r"^model=(?P<model>[a-z0-9][a-z0-9./_-]*)\s+effort=(?P<effort>[a-z]+)$",
    re.IGNORECASE,
)


class InvalidBioModelDirective(ValueError):
    """Raised when a bio starts a model directive but does not select a supported target."""


def parse_bio_model_directive(bio: str) -> tuple[str, str] | None:
    """Parse an optional `model=<id> effort=<level>` bio override.

    Ordinary bio text keeps its historic meaning (assistant enabled, use /model).
    A malformed or unsupported explicit directive is an error so it cannot silently
    run a different model than the one the owner intended.
    """
    normalized = bio.strip()
    if not normalized.casefold().startswith(BIO_DIRECTIVE_PREFIX):
        return None
    match = BIO_DIRECTIVE_RE.fullmatch(normalized)
    if match is None:
        raise InvalidBioModelDirective("invalid format")
    model = match.group("model").casefold()
    effort = match.group("effort").casefold()
    option = LIST1_BY_MODEL.get(model)
    if option is None:
        raise InvalidBioModelDirective("unsupported model")
    if effort not in option.efforts:
        raise InvalidBioModelDirective("unsupported effort")
    return model, effort


def model_default_effort(model: str) -> str:
    option = LIST1_BY_MODEL.get(model.casefold())
    return option.default_effort if option else "medium"


def model_efforts(model: str) -> tuple[str, ...]:
    option = LIST1_BY_MODEL.get(model.casefold())
    if option:
        return option.efforts
    return EFFORTS_BY_BACKEND["opencode" if model.casefold().startswith("opencode/") else "codex"]
