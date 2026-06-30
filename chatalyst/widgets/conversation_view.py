from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from chatalyst.core.markdown_renderer import MarkdownRenderer
from chatalyst.core.models import Conversation, Message, MessageRole


class ConversationView(VerticalScroll):
    DEFAULT_CSS = """
    ConversationView {
        border: solid $secondary;
        height: 1fr;
        padding: 0 1;
    }
    ConversationView .role {
        text-style: bold;
        margin-top: 1;
    }
    ConversationView .message {
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.renderer = MarkdownRenderer()
        self._header = Static("Open a conversation", classes="role")
        self._body = Static("")
        self.last_markdown = ""

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._body

    def show_conversation(self, conversation: Conversation, messages: list[Message]) -> None:
        self._header.update(f"# {conversation.title}")
        renderables = []
        for message in messages:
            role = self._role_label(message.role)
            renderables.append(f"**{role}:**\n\n{message.markdown}")
        self.last_markdown = "\n\n---\n\n".join(renderables)
        self._body.update(self.renderer.render(self.last_markdown))

    def show_streaming_message(self, conversation: Conversation, messages: list[Message]) -> None:
        self.show_conversation(conversation, messages)

    def show_diagnostic(self, title: str, body: str) -> None:
        self._header.update(title)
        self.last_markdown = body
        self._body.update(self.renderer.render(body))

    def _role_label(self, role: MessageRole) -> str:
        if role is MessageRole.USER:
            return "User"
        if role is MessageRole.ASSISTANT:
            return "ChatGPT"
        return role.value.title()
