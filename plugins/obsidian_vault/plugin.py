from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chatalyst.core.models import Message, MessageRole

JsonObject = dict[str, Any]

DEFAULT_REQUIREMENTS = ("athena-visual-qa-gate",)
DEFAULT_FOLDER = "Chatalyst/Requirements"


@dataclass(frozen=True)
class ObsidianSettings:
    vault_path: Path | None
    folder: str
    requirements: tuple[str, ...]


class ObsidianVaultPlugin:
    name = "obsidian_vault"
    description = "Capture requirement-bearing requests into a local Obsidian vault."

    def mcp_tools(self, _context: object) -> list[JsonObject]:
        return [
            {
                "name": "status",
                "description": "Report Obsidian vault capture plugin configuration.",
                "input_schema": {"type": "object", "properties": {}},
                "read_only": True,
                "handler": self.status,
            },
            {
                "name": "capture_request",
                "description": "Capture a requirement-bearing request into the Obsidian vault.",
                "input_schema": {
                    "type": "object",
                    "required": ["body"],
                    "properties": {
                        "body": {"type": "string"},
                        "title": {"type": "string"},
                        "requirements": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "conversation_id": {"type": "string"},
                        "message_id": {"type": "string"},
                        "source": {"type": "string"},
                    },
                },
                "read_only": False,
                "handler": self.capture_request,
            },
        ]

    def status(self, _context: object, _arguments: JsonObject) -> JsonObject:
        settings = self._settings()
        writable = False
        if settings.vault_path is not None:
            writable = settings.vault_path.exists() and os.access(settings.vault_path, os.W_OK)
        return {
            "configured": settings.vault_path is not None,
            "vault_path": str(settings.vault_path) if settings.vault_path else None,
            "vault_writable": writable,
            "folder": settings.folder,
            "requirements": list(settings.requirements),
        }

    def capture_request(self, _context: object, arguments: JsonObject) -> JsonObject:
        body = self._bounded_str(arguments.get("body"), "body", maximum=200_000)
        requirements = self._requirements_from_arguments(arguments) or self._matched_requirements(
            body
        )
        if not requirements:
            return {
                "captured": False,
                "reason": "no_configured_requirement_marker_found",
                "requirements": [],
            }
        title = self._optional_str(arguments.get("title")) or "Chatalyst Requirement Capture"
        return self._capture(
            body=body,
            title=title,
            requirements=requirements,
            conversation_id=self._optional_str(arguments.get("conversation_id")),
            message_id=self._optional_str(arguments.get("message_id")),
            source=self._optional_str(arguments.get("source")) or "mcp_tool",
        )

    def on_message_cached(self, _context: object, message: Message) -> None:
        if message.role is not MessageRole.USER:
            return
        requirements = self._matched_requirements(message.markdown)
        if not requirements:
            return
        self._capture(
            body=message.markdown,
            title="Chatalyst Requirement Capture",
            requirements=requirements,
            conversation_id=message.conversation_id,
            message_id=message.id,
            source="message_cached",
        )

    def _capture(
        self,
        *,
        body: str,
        title: str,
        requirements: tuple[str, ...],
        conversation_id: str | None,
        message_id: str | None,
        source: str,
    ) -> JsonObject:
        settings = self._settings()
        if settings.vault_path is None:
            return {
                "captured": False,
                "reason": "vault_not_configured",
                "requirements": list(requirements),
            }
        target_dir = settings.vault_path / self._safe_relative_folder(settings.folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            f"{conversation_id or ''}:{message_id or ''}:{body}".encode()
        ).hexdigest()[:12]
        slug = self._slugify("-".join(requirements) or title)
        target = target_dir / f"{slug}-{digest}.md"
        if target.exists():
            return {
                "captured": True,
                "path": str(target),
                "requirements": list(requirements),
                "existing": True,
            }
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(
            self._note_body(
                title=title,
                body=body,
                requirements=requirements,
                conversation_id=conversation_id,
                message_id=message_id,
                source=source,
                captured_at=datetime.now(UTC),
            ),
            encoding="utf-8",
        )
        tmp.replace(target)
        target.chmod(0o600)
        return {
            "captured": True,
            "path": str(target),
            "requirements": list(requirements),
            "existing": False,
        }

    def _settings(self) -> ObsidianSettings:
        config = self._local_config()
        vault = (
            os.environ.get("CHATALYST_OBSIDIAN_VAULT")
            or str(config.get("vault_path") or "").strip()
        )
        folder = (
            os.environ.get("CHATALYST_OBSIDIAN_FOLDER")
            or str(config.get("folder") or "").strip()
            or DEFAULT_FOLDER
        )
        requirements = self._requirements_from_env() or self._requirements_from_config(config)
        vault_path = Path(vault).expanduser().resolve() if vault else None
        return ObsidianSettings(
            vault_path=vault_path,
            folder=folder,
            requirements=requirements or DEFAULT_REQUIREMENTS,
        )

    def _local_config(self) -> JsonObject:
        path = Path(__file__).resolve().parent / "plugin_config.json"
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _matched_requirements(self, body: str) -> tuple[str, ...]:
        lowered = body.casefold()
        return tuple(
            requirement
            for requirement in self._settings().requirements
            if requirement.casefold() in lowered
        )

    def _requirements_from_arguments(self, arguments: JsonObject) -> tuple[str, ...]:
        raw = arguments.get("requirements")
        if not isinstance(raw, list):
            return ()
        return tuple(
            value.strip()
            for value in raw
            if isinstance(value, str) and value.strip()
        )

    def _requirements_from_env(self) -> tuple[str, ...]:
        raw = os.environ.get("CHATALYST_OBSIDIAN_REQUIREMENTS", "")
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    def _requirements_from_config(self, config: JsonObject) -> tuple[str, ...]:
        raw = config.get("requirements")
        if not isinstance(raw, list):
            return ()
        return tuple(value.strip() for value in raw if isinstance(value, str) and value.strip())

    def _note_body(
        self,
        *,
        title: str,
        body: str,
        requirements: tuple[str, ...],
        conversation_id: str | None,
        message_id: str | None,
        source: str,
        captured_at: datetime,
    ) -> str:
        requirement_lines = "\n".join(f"  - {self._yaml_string(item)}" for item in requirements)
        return (
            "---\n"
            f"title: {self._yaml_string(title)}\n"
            f"captured_at: {self._yaml_string(captured_at.isoformat())}\n"
            f"source: {self._yaml_string(source)}\n"
            f"conversation_id: {self._yaml_string(conversation_id)}\n"
            f"message_id: {self._yaml_string(message_id)}\n"
            "requirements:\n"
            f"{requirement_lines}\n"
            "---\n\n"
            f"# {title}\n\n"
            "## Request\n\n"
            f"{body.strip()}\n"
        )

    def _bounded_str(self, value: object, field: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{field} must not be empty")
        if len(stripped) > maximum:
            raise ValueError(f"{field} exceeds {maximum} characters")
        return stripped

    def _optional_str(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    def _safe_relative_folder(self, value: str) -> Path:
        parts = [
            self._slugify(part)
            for part in Path(value).parts
            if part not in {"", ".", "..", "/", "\\"}
        ]
        return Path(*parts) if parts else Path(DEFAULT_FOLDER)

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
        return slug[:80] or "capture"

    def _yaml_string(self, value: str | None) -> str:
        if value is None:
            return "null"
        return json.dumps(value)


def create_plugin() -> ObsidianVaultPlugin:
    return ObsidianVaultPlugin()
