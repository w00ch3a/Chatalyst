from __future__ import annotations

from textual.widgets import Label, ListItem, ListView

from chatalyst.core.models import Conversation


class ConversationListItem(ListItem):
    def __init__(self, conversation: Conversation) -> None:
        prefix = "★ " if conversation.is_pinned else ""
        project = f" [{conversation.project_name}]" if conversation.project_name else ""
        super().__init__(Label(f"{prefix}{conversation.title}{project}"))
        self.conversation = conversation


class ChatList(ListView):
    DEFAULT_CSS = """
    ChatList {
        border: solid $primary;
        width: 36;
        height: 1fr;
    }
    """

    def load_conversations(self, conversations: list[Conversation]) -> None:
        self.clear()
        for conversation in conversations:
            self.append(ConversationListItem(conversation))

    @property
    def selected_conversation(self) -> Conversation | None:
        item = self.highlighted_child
        if isinstance(item, ConversationListItem):
            return item.conversation
        return None
