from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView


class CommandPalette(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    CommandPalette > Vertical {
        width: 70%;
        height: auto;
        max-height: 80%;
        margin: 2 8;
        border: thick $accent;
        background: $panel;
        padding: 1;
    }
    """

    COMMANDS = (
        ("open", "Open Chat"),
        ("search", "Search"),
        ("export", "Export"),
        ("obsidian", "Export to Obsidian"),
        ("bookmark", "Bookmark"),
        ("notes", "Open Notes"),
        ("refresh", "Refresh"),
        ("offline", "Offline Mode"),
        ("recent", "Recent Chats"),
        ("split", "Split View"),
        ("stage", "Stage Last Code"),
    )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Command Palette")
            list_view = ListView(id="commands")
            for command, label in self.COMMANDS:
                item = ListItem(Label(label))
                item.command = command
                list_view.append(item)
            yield list_view

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(getattr(event.item, "command", None))

    def action_cancel(self) -> None:
        self.dismiss(None)
