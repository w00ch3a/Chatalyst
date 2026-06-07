from __future__ import annotations

from textual.widgets import Static

from chatalyst.core.models import BrowserSessionStatus, Conversation


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text;
    }
    """

    def update_status(
        self,
        status: BrowserSessionStatus,
        *,
        conversation: Conversation | None = None,
        sync: str = "idle",
    ) -> None:
        selected = conversation.title if conversation else "none"
        project = conversation.project_name if conversation and conversation.project_name else "-"
        offline = "offline" if status.offline else "online"
        self.update(
            f"Login: {status.login_state.value} | Browser: {status.browser_state.value} | "
            f"Sync: {sync} | Selected: {selected} | Project: {project} | {offline}"
        )
