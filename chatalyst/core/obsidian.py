from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, MessageRole

DEFAULT_OBSIDIAN_FOLDER = "Chatalyst/Exports"


class ObsidianDestinationRequired(ValueError):
    """Raised when no vault or markdown destination is configured."""


@dataclass(frozen=True)
class ObsidianExportResult:
    path: Path
    destination_type: str
    used_configured_vault: bool


class ObsidianExportService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def has_configured_vault(self) -> bool:
        return self._configured_vault() is not None

    def export_conversation(
        self,
        conversation: Conversation,
        messages: list[Message],
        *,
        destination: str | Path | None = None,
    ) -> ObsidianExportResult:
        body = self._render_conversation(conversation.title, messages)
        return self.export_markdown(
            title=conversation.title,
            body=body,
            conversation_id=conversation.id,
            destination=destination,
            source="tui_conversation",
        )

    def export_message(
        self,
        conversation: Conversation,
        message: Message,
        *,
        destination: str | Path | None = None,
    ) -> ObsidianExportResult:
        role = self._role_label(message.role)
        title = f"{conversation.title} - {role}"
        body = f"# {title}\n\n{message.markdown.strip()}\n"
        return self.export_markdown(
            title=title,
            body=body,
            conversation_id=conversation.id,
            message_id=message.id,
            destination=destination,
            source="tui_message",
        )

    def export_markdown(
        self,
        *,
        title: str,
        body: str,
        conversation_id: str | None = None,
        message_id: str | None = None,
        destination: str | Path | None = None,
        source: str = "tui",
    ) -> ObsidianExportResult:
        markdown = self._with_frontmatter(
            title=title,
            body=body,
            conversation_id=conversation_id,
            message_id=message_id,
            source=source,
        )
        target, destination_type, used_configured = self._target_path(
            title=title,
            body=markdown,
            destination=destination,
        )
        parent_existed = target.parent.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            target.parent.chmod(0o700)
        final = self._non_overwriting_path(target)
        tmp = final.with_suffix(final.suffix + ".tmp")
        tmp.write_text(markdown, encoding="utf-8")
        tmp.replace(final)
        final.chmod(0o600)
        return ObsidianExportResult(
            path=final,
            destination_type=destination_type,
            used_configured_vault=used_configured,
        )

    def _target_path(
        self,
        *,
        title: str,
        body: str,
        destination: str | Path | None,
    ) -> tuple[Path, str, bool]:
        if destination is not None and str(destination).strip():
            path = Path(destination).expanduser()
            if path.suffix.casefold() == ".md":
                return path.resolve(), "markdown_file", False
            return self._vault_note_path(path.resolve(), title=title, body=body), "vault", False
        vault = self._configured_vault()
        if vault is None:
            raise ObsidianDestinationRequired(
                "No Obsidian vault configured. Provide a vault folder or .md file path."
            )
        return self._vault_note_path(vault, title=title, body=body), "vault", True

    def _configured_vault(self) -> Path | None:
        raw = os.environ.get("CHATALYST_OBSIDIAN_VAULT")
        if raw is None:
            raw = str(self._plugin_config().get("vault_path") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    def _configured_folder(self) -> str:
        env_folder = os.environ.get("CHATALYST_OBSIDIAN_FOLDER")
        if env_folder and env_folder.strip():
            return env_folder.strip()
        config_folder = str(self._plugin_config().get("folder") or "").strip()
        return config_folder or DEFAULT_OBSIDIAN_FOLDER

    def _plugin_config(self) -> dict[str, object]:
        path = self.config.plugins_dir / "obsidian_vault" / "plugin_config.json"
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _vault_note_path(self, vault: Path, *, title: str, body: str) -> Path:
        folder = self._safe_relative_folder(self._configured_folder())
        digest = hashlib.sha256(body.encode()).hexdigest()[:12]
        return vault / folder / f"{self._slug(title)}-{digest}.md"

    def _with_frontmatter(
        self,
        *,
        title: str,
        body: str,
        conversation_id: str | None,
        message_id: str | None,
        source: str,
    ) -> str:
        cleaned = body.strip()
        return (
            "---\n"
            f"title: {self._yaml_string(title)}\n"
            f"exported_at: {self._yaml_string(datetime.now(UTC).isoformat())}\n"
            f"source: {self._yaml_string(source)}\n"
            f"conversation_id: {self._yaml_string(conversation_id)}\n"
            f"message_id: {self._yaml_string(message_id)}\n"
            "---\n\n"
            f"{cleaned}\n"
        )

    def _render_conversation(self, title: str, messages: list[Message]) -> str:
        chunks = [f"# {title}", ""]
        for message in messages:
            chunks.extend((f"## {self._role_label(message.role)}", "", message.markdown, ""))
        return "\n".join(chunks).rstrip() + "\n"

    def _role_label(self, role: MessageRole) -> str:
        if role is MessageRole.USER:
            return "User"
        if role is MessageRole.ASSISTANT:
            return "ChatGPT"
        return role.value.title()

    def _non_overwriting_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        index = 2
        while True:
            candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    def _safe_relative_folder(self, value: str) -> Path:
        parts = [
            self._safe_path_part(part)
            for part in Path(value).parts
            if part not in {"", ".", "..", "/", "\\"}
        ]
        return Path(*parts) if parts else Path(DEFAULT_OBSIDIAN_FOLDER)

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-").lower()
        return slug[:90] or "chatalyst-export"

    def _safe_path_part(self, value: str) -> str:
        part = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
        return part[:90] or "folder"

    def _yaml_string(self, value: str | None) -> str:
        if value is None:
            return "null"
        return json.dumps(value)
