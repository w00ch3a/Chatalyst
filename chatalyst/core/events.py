from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EventType(StrEnum):
    BROWSER_STARTED = "browser_started"
    BROWSER_STOPPED = "browser_stopped"
    LOGIN_STATE_CHANGED = "login_state_changed"
    SYNC_STARTED = "sync_started"
    SYNC_FINISHED = "sync_finished"
    SYNC_FAILED = "sync_failed"
    CONVERSATION_OPENED = "conversation_opened"
    MESSAGE_STREAMED = "message_streamed"
    SEARCH_COMPLETED = "search_completed"
    SELECTOR_FAILED = "selector_failed"


@dataclass(frozen=True)
class WorkspaceEvent:
    type: EventType
    message: str
    payload: dict[str, object] | None = None


class EventSink(Protocol):
    def emit(self, event: WorkspaceEvent) -> None:
        """Receive a workspace event."""


class NullEventSink:
    def emit(self, event: WorkspaceEvent) -> None:
        _ = event
