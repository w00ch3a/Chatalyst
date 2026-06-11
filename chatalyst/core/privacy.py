from __future__ import annotations

import re
from typing import Any, Final

PROJECT_URL_RE: Final = re.compile(r"https://(?:chatgpt\.com|chat\.openai\.com)/g/[^\\s\"']+")
PROJECT_ID_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_-])g-[A-Za-z0-9_-]{6,}|"
    r"(?<![A-Za-z0-9_-])proj-[A-Za-z0-9_-]{6,}"
)


def redact_project_reference(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = PROJECT_URL_RE.sub("https://chatgpt.com/g/[redacted]", value)
    redacted = PROJECT_ID_RE.sub("[redacted-project-id]", redacted)
    return redacted


def redact_project_refs(value: Any) -> Any:
    if isinstance(value, str):
        return redact_project_reference(value)
    if isinstance(value, list):
        return [redact_project_refs(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_project_refs(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_project_refs(item) for key, item in value.items()}
    return value
