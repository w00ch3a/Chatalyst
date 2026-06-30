from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from chatalyst.core.models import Snippet


class SnippetPanel(ModalScreen[str | None]):
    DEFAULT_CSS = """
    SnippetPanel > Vertical {
        width: 80%;
        height: 80%;
        margin: 2 4;
        border: thick $accent;
        background: $panel;
        padding: 1;
    }
    SnippetPanel Static {
        height: 1fr;
        overflow: auto;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("r", "run", "Run"),
        ("s", "save", "Save"),
        ("c", "copy", "Copy"),
    ]

    def __init__(self, snippet: Snippet) -> None:
        super().__init__()
        self.snippet = snippet

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"Snippet: {self.snippet.language or 'text'} | "
                f"mode: {self.snippet.run_mode.value} | path: {self.snippet.path or '-'}"
            )
            yield Static(f"```{self.snippet.language or ''}\n{self.snippet.body}\n```")
            yield Button("Run", id="run-snippet", variant="error")
            yield Button("Save", id="save-snippet", variant="primary")
            yield Button("Copy", id="copy-snippet")
            yield Button("Cancel", id="cancel-snippet")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-snippet":
            self.dismiss("run")
        elif event.button.id == "save-snippet":
            self.dismiss("save")
        elif event.button.id == "copy-snippet":
            self.dismiss("copy")
        else:
            self.dismiss(None)

    def action_run(self) -> None:
        self.dismiss("run")

    def action_save(self) -> None:
        self.dismiss("save")

    def action_copy(self) -> None:
        self.dismiss("copy")

    def action_cancel(self) -> None:
        self.dismiss(None)
