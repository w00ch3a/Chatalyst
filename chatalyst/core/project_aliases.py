from __future__ import annotations

import json
from dataclasses import dataclass

from loguru import logger

from chatalyst.core.config import AppConfig


@dataclass(frozen=True)
class ProjectReference:
    display: str
    resolved: str
    alias_used: str | None = None


class ProjectAliasResolver:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def resolve(self, reference: str | None) -> ProjectReference | None:
        if reference is None:
            return None
        stripped = reference.strip()
        if not stripped:
            return ProjectReference(display=reference, resolved=reference)
        aliases = self._load_aliases()
        target = aliases.get(stripped) or aliases.get(stripped.casefold())
        if target:
            return ProjectReference(display=stripped, resolved=target.strip(), alias_used=stripped)
        return ProjectReference(display=stripped, resolved=stripped)

    def _load_aliases(self) -> dict[str, str]:
        path = self.config.project_aliases_path
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Unable to read project aliases from {}", path)
            return {}
        if not isinstance(raw, dict):
            logger.warning("Project aliases file {} must contain a JSON object.", path)
            return {}
        aliases: dict[str, str] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
                aliases[key.strip()] = value.strip()
                aliases[key.strip().casefold()] = value.strip()
        return aliases
