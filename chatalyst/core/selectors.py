from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectorGroup:
    name: str
    candidates: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class SelectorDiagnostic:
    selector_group: str
    attempted: tuple[str, ...]
    url: str | None
    message: str


class SelectorCatalog:
    """DOM selectors for human-equivalent ChatGPT browser automation.

    Selectors intentionally target visible DOM and accessible controls. They are not network
    endpoints and do not rely on private backend contracts.
    """

    conversation_links = SelectorGroup(
        name="conversation_links",
        candidates=(
            'a[href*="/c/"]',
            'nav a[href*="/c/"]',
            '[data-testid="history-item"] a',
        ),
        description="Sidebar/history links for existing conversations.",
    )
    composer = SelectorGroup(
        name="composer",
        candidates=(
            'div[contenteditable="true"][data-testid*="prompt"]',
            'div[contenteditable="true"]',
            'textarea[data-testid*="prompt"]',
            "textarea",
        ),
        description="Prompt composer.",
    )
    send_button = SelectorGroup(
        name="send_button",
        candidates=(
            'button[data-testid="send-button"]',
            'button[aria-label="Send prompt"]',
            'button[aria-label*="Send"]',
        ),
        description="Send prompt button.",
    )
    file_input = SelectorGroup(
        name="file_input",
        candidates=('input[type="file"]',),
        description="Hidden file picker input for ChatGPT attachments.",
    )
    attach_button = SelectorGroup(
        name="attach_button",
        candidates=(
            'button[aria-label*="Attach"]',
            'button[aria-label*="Upload"]',
            'button[aria-label*="Add photos"]',
            'button[aria-label*="Add files"]',
        ),
        description="Attachment button used to reveal the file picker.",
    )
    stop_button = SelectorGroup(
        name="stop_button",
        candidates=(
            'button[data-testid="stop-button"]',
        ),
        description="Stop generating button used as a streaming marker.",
    )
    message_blocks = SelectorGroup(
        name="message_blocks",
        candidates=(
            '[data-message-author-role]',
            'article[data-testid*="conversation-turn"]',
            "main article",
        ),
        description="Conversation message blocks.",
    )
    new_chat_button = SelectorGroup(
        name="new_chat_button",
        candidates=(
            'a[href="/"]',
            'a[aria-label*="New chat"]',
            'button[aria-label*="New chat"]',
            '[data-testid="create-new-chat-button"]',
        ),
        description="New conversation control.",
    )
    login_markers = SelectorGroup(
        name="login_markers",
        candidates=(
            'button:has-text("Log in")',
            'button:has-text("Sign up")',
            'a[href*="auth"]',
        ),
        description="Visible logged-out controls.",
    )
    project_labels = SelectorGroup(
        name="project_labels",
        candidates=(
            '[data-testid*="project"]',
            'a[href*="/g/"]',
            'nav [role="treeitem"]',
        ),
        description="Visible project/group labels where available.",
    )

    def all_groups(self) -> tuple[SelectorGroup, ...]:
        return (
            self.conversation_links,
            self.composer,
            self.send_button,
            self.stop_button,
            self.message_blocks,
            self.new_chat_button,
            self.login_markers,
            self.project_labels,
        )
