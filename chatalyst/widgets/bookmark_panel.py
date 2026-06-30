from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from chatalyst.core.models import Bookmark


class BookmarkPanel(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    BookmarkPanel > Vertical {
        width: 70%;
        height: 70%;
        margin: 2 8;
        border: thick $secondary;
        background: $panel;
        padding: 1;
    }
    """

    def __init__(self, bookmarks: list[Bookmark]) -> None:
        super().__init__()
        self.bookmarks = bookmarks

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Bookmarks")
            list_view = ListView()
            for bookmark in self.bookmarks:
                item = ListItem(Label(bookmark.label or bookmark.message_id))
                item.conversation_id = bookmark.conversation_id
                list_view.append(item)
            yield list_view

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(getattr(event.item, "conversation_id", None))

    def action_cancel(self) -> None:
        self.dismiss(None)
