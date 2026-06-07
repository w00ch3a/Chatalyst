from __future__ import annotations

import html
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from markdown_it import MarkdownIt

from chatalyst.core.cache import ChatCache
from chatalyst.core.models import ExportRecord


class ExportFormat(StrEnum):
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    TXT = "txt"


class ExportService:
    def __init__(self, cache: ChatCache, exports_dir: Path) -> None:
        self.cache = cache
        self.exports_dir = exports_dir
        self._markdown = MarkdownIt("commonmark", {"html": False}).enable("table")

    def export_conversation(
        self,
        conversation_id: str,
        export_format: ExportFormat,
        selected_message_ids: list[str] | None = None,
    ) -> Path:
        conversation = self.cache.get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        messages = self.cache.list_messages(conversation_id, selected_message_ids)
        selected = selected_message_ids or []
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.chmod(0o700)
        slug = self._slug(conversation.title or conversation.id)
        path = self._unique_path(slug, self._extension(export_format))
        if export_format is ExportFormat.MARKDOWN:
            path.write_text(self._render_markdown(conversation.title, messages), encoding="utf-8")
        elif export_format is ExportFormat.HTML:
            path.write_text(self._render_html(conversation.title, messages), encoding="utf-8")
        elif export_format is ExportFormat.JSON:
            path.write_text(
                json.dumps(
                    {
                        "conversation": conversation.model_dump(mode="json"),
                        "messages": [message.model_dump(mode="json") for message in messages],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        elif export_format is ExportFormat.TXT:
            path.write_text(self._render_txt(messages), encoding="utf-8")
        else:
            raise ValueError(f"Unsupported export format: {export_format}")
        path.chmod(0o600)
        self.cache.record_export(
            ExportRecord(
                conversation_id=conversation_id,
                format=export_format.value,
                path=str(path),
                selected_message_ids=selected,
            )
        )
        return path

    def _render_markdown(self, title: str, messages: object) -> str:
        chunks = [f"# {title}", ""]
        for message in messages:
            chunks.extend((f"## {message.role.value.title()}", "", message.markdown, ""))
        return "\n".join(chunks).rstrip() + "\n"

    def _render_html(self, title: str, messages: object) -> str:
        body = self._markdown.render(self._render_markdown(title, messages))
        return (
            "<!doctype html>\n"
            "<html><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
            "max-width:860px;margin:2rem auto;line-height:1.55}"
            "pre{padding:1rem;overflow:auto;background:#111827;color:#f9fafb}"
            "code{font-family:SFMono-Regular,Consolas,monospace}</style>"
            "</head><body>"
            f"{body}</body></html>\n"
        )

    def _render_txt(self, messages: object) -> str:
        lines: list[str] = []
        for message in messages:
            lines.append(f"{message.role.value.title()}:")
            lines.append(message.markdown)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _extension(self, export_format: ExportFormat) -> str:
        if export_format is ExportFormat.MARKDOWN:
            return "md"
        return export_format.value

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
        return slug or "conversation"

    def _unique_path(self, slug: str, extension: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = self.exports_dir / f"{slug}-{stamp}.{extension}"
        if not candidate.exists():
            return candidate
        index = 2
        while True:
            candidate = self.exports_dir / f"{slug}-{stamp}-{index}.{extension}"
            if not candidate.exists():
                return candidate
            index += 1
