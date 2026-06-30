from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView

from chatalyst.core.models import SearchResult


class SearchDialog(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    SearchDialog > Vertical {
        width: 80%;
        height: 70%;
        margin: 2 4;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    """

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        super().__init__()
        self.results = results or []
        self.input = Input(placeholder="/search query")
        self.list_view = ListView()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Search")
            yield self.input
            yield self.list_view

    def on_mount(self) -> None:
        self.input.focus()
        self.set_results(self.results)

    def set_results(self, results: list[SearchResult]) -> None:
        self.results = results
        self.list_view.clear()
        for result in results:
            item = ListItem(Label(f"{result.title} | {result.kind} | {result.snippet}"))
            item.conversation_id = result.conversation_id
            self.list_view.append(item)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        conversation_id = getattr(event.item, "conversation_id", None)
        self.dismiss(conversation_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
