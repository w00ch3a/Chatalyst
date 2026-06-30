from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, TextArea

from chatalyst.core.models import Note


class NotesPanel(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    NotesPanel > Vertical {
        width: 70%;
        height: 70%;
        margin: 2 8;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    """

    def __init__(self, notes: list[Note]) -> None:
        super().__init__()
        self.notes = notes
        self.editor = TextArea("\n\n".join(note.body for note in notes))

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.editor
            yield Button("Save", id="save-notes", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-notes":
            self.dismiss(self.editor.text)

    def action_cancel(self) -> None:
        self.dismiss(None)
