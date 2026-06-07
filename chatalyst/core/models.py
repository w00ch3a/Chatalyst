from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    UNKNOWN = "unknown"


class BrowserState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"


class LoginState(StrEnum):
    UNKNOWN = "unknown"
    LOGGED_OUT = "logged_out"
    LOGGED_IN = "logged_in"


class SyncStatus(StrEnum):
    NEVER = "never"
    SYNCING = "syncing"
    CACHED = "cached"
    STALE = "stale"
    FAILED = "failed"


class SnippetRunMode(StrEnum):
    COPY_ONLY = "copy_only"
    SHELL_SCRIPT = "shell_script"
    PYTHON_SCRIPT = "python_script"


class Project(BaseModel):
    id: str
    name: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Conversation(BaseModel):
    id: str
    title: str
    url: str | None = None
    chat_identifier: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_opened_at: datetime | None = None
    most_opened: int = 0
    is_pinned: bool = False
    sync_status: SyncStatus = SyncStatus.NEVER


class CodeBlock(BaseModel):
    language: str | None = None
    code: str
    ordinal: int


class Message(BaseModel):
    id: str
    conversation_id: str
    role: MessageRole
    markdown: str
    ordinal: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    is_streaming: bool = False
    code_blocks: list[CodeBlock] = Field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len([part for part in self.markdown.split() if part.strip()])


class Note(BaseModel):
    id: str | None = None
    conversation_id: str
    body: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Tag(BaseModel):
    id: str | None = None
    name: str
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def normalized(self) -> str:
        return self.name.strip().lower().lstrip("#")


class Bookmark(BaseModel):
    id: str | None = None
    conversation_id: str
    message_id: str
    label: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ExportRecord(BaseModel):
    id: str | None = None
    conversation_id: str
    format: str
    path: str
    selected_message_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SyncState(BaseModel):
    key: str
    value: str
    updated_at: datetime = Field(default_factory=utc_now)


class SnippetStatus(StrEnum):
    STAGED = "staged"
    SAVED = "saved"
    RAN = "ran"
    FAILED = "failed"


class Snippet(BaseModel):
    id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    body: str
    language: str | None = None
    run_mode: SnippetRunMode = SnippetRunMode.COPY_ONLY
    path: str | None = None
    status: SnippetStatus = SnippetStatus.STAGED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SearchResult(BaseModel):
    conversation_id: str
    kind: str
    title: str
    snippet: str
    score: float
    message_id: str | None = None


class ConversationStats(BaseModel):
    conversation_id: str
    message_count: int
    word_count: int
    created_at: datetime | None
    last_updated: datetime | None
    project_name: str | None
    sync_status: SyncStatus


class BrowserSessionStatus(BaseModel):
    browser_state: BrowserState = BrowserState.STOPPED
    login_state: LoginState = LoginState.UNKNOWN
    offline: bool = False
    diagnostic: str | None = None


JsonDict = dict[str, Any]
