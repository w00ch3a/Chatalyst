from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class ObsidianExportDialog(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ObsidianExportDialog > Vertical {
        width: 78%;
        height: auto;
        margin: 2 6;
        border: thick $accent;
        background: $panel;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Obsidian vault folder or .md file path")
            yield Input(placeholder="~/Documents/Obsidian/My Vault or ~/Desktop/chat.md")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
