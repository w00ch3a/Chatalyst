from __future__ import annotations

from textual.widgets import Input


class MessageInput(Input):
    DEFAULT_CSS = """
    MessageInput {
        dock: bottom;
        height: 3;
        border: tall $accent;
    }
    """

    def __init__(self) -> None:
        super().__init__(placeholder="> prompt here")
